import os,sys
from parse import parse
from abc import ABC, abstractmethod
import time
import json
import redis
import datetime

from ATATools import ata_control, logger_defaults, ata_if

from hashpipe_keyvalues import HashpipeKeyValues

from SNAPobs.snap_hpguppi import snap_hpguppi_defaults as hpguppi_defaults
from SNAPobs.snap_hpguppi import record_in as hpguppi_record_in
from SNAPobs.snap_hpguppi import auxillary as hpguppi_auxillary


PROJECTID_FNAME = "./projects.json"
BACKENDS_FNAME = "./backends.json"
POSTPROCESSORS_FNAME = "./postprocessors.json"

def wait_until(target_time: datetime.datetime):
    """
    Pauses execution until the specified target time.

    Parameters:
    - target_time (datetime.datetime): The datetime to wait until.

    Raises:
    - ValueError: If target_time is in the past.
    """
    now = datetime.datetime.now()

    if target_time <= now:
        print("CAREFUL TIME WAS BEFORE NOW")
        return
        #raise ValueError("Target time is in the past. Please provide a future time.")

    # Calculate the remaining time in seconds
    remaining_time = (target_time - now).total_seconds()
    print(f"Waiting for {remaining_time} seconds until {target_time}...")

    # Sleep for the remaining time
    time.sleep(remaining_time)
    print("Reached target time:", target_time)


def most_common(lst):
    return max(set(lst), key=lst.count)


def load_mapping(fname):
    with open(fname, 'r') as json_file:
        mapping = json.load(json_file)
    return mapping


def get_current_backend(hp_targets):
    redis_obj = redis.Redis(host='redishost', decode_responses=True)
    kvs = []

    for node, instances in hp_targets.items():
        for instance in instances:
            kvs.append(HashpipeKeyValues(node, instance, redis_obj))

    backend = list(set(kv.get("HPCONFIG") for kv in kvs))
    assert len(backend) == 1, "More than 1 backend detected for targets..."

    return backend[0]


class Executable(ABC):
    def __init__(self, config, write_status):
        self.config = config
        self.write_status = write_status

    @abstractmethod
    def execute(self):
        pass

    def check_consistency(self, needed_keys):
        for key in needed_keys:
            if key not in self.config.keys():
                raise RuntimeError("Key: %s not in config keys" %key)


class ReserveAntennas(Executable):
    def execute(self):
        ant_list = self.config['ant_list']
        ata_control.reserve_antennas(ant_list)


class SetFreqTunning(Executable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        needed_keys = ["ant_list", "RFgain", "IFgain",
                       "EQlevel", "TuningA", "TuningB",
                       "TuningC", "TuningD"]
        self.check_consistency(needed_keys)

    def execute(self):
        # Get all the needed LOs
        los   = []
        freqs = []
        for t in ["a", "b", "c", "d"]: 
            t_config = 'Tuning_'+t.upper()
            if t_config in self.config:
                los.append(t)
                freqs.append(float(self.config[t_config]))
        if los:
            self.write_status("Setting frequencies for LOs: %s" %los)
            max_freq = max(freqs)
            lo_max_freq = los[freqs.index(max_freq)]

            for lo, freq in zip(los, freqs):
                if lo == max_freq:
                    ata_control.set_freq(freq, self.config['ant_list'], 
                                         lo=lo)
                else:
                    ata_control.set_freq(freq, self.config['ant_list'], 
                                         lo=lo, nofocus=True)
            time.sleep(20)

        if bool(int(self.config['RFgain'])):
            self.write_status("Tunning RF:")
            ata_control.autotune(self.config['ant_list'])

        if bool(int(self.config['IFgain'])):
            self.write_status("Tuning IF:")
            ata_if.tune_if(self.config['ant_list'], los=los)

        if bool(int(self.config['EQlevel'])):
            pass


class SetBackend(Executable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        needed_keys = ["ProjectID", "Backend", 
                "Postprocessor", "hp_targets"]
        self.check_consistency(needed_keys)

    def execute(self):
        projectid_mapping      = load_mapping(PROJECTID_FNAME)
        backends_mapping       = load_mapping(BACKENDS_FNAME)
        postprocessors_mapping = load_mapping(POSTPROCESSORS_FNAME)

        backend_config  = backends_mapping[self.config['Backend']]
        postproc_script = postprocessors_mapping[self.config['Postprocessor']]

        # Set backend
        os.system(f"ansible-playbook {backend_config}")

        if self.config['Backend'].upper().startswith("XGPU"):
            res = parse('xGPU_{xtimeint}s', self.config['Backend'])
            xtimeint = float(res['xtimeint'])
            keyval_dict = {'XTIMEINT': xtimeint}

            hp_targets = self.config['hp_targets']

            hpguppi_auxillary.publish_keyval_dict_to_redis(keyval_dict,
                            hp_targets, postproc=False)


        # Set postprocessor
        os.system(postproc_script)


class Wait(Executable):
    def __init__(self, *args, **kwards):
        super().__init__(*args, **kwargs)
        self.check_consistence(needed_keys)

        
class TrackAndObserve(Executable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        needed_keys = ["ant_list", "hp_targets", "Source",
                "ObsTime"]
        self.check_consistency(needed_keys)

    def execute(self):
        ant_list = self.config['ant_list']
        source   = self.config['Source']

        if source.upper() != "NONE":
            self.write_status(f"Tracking source {source}")
            ata_control.make_and_track_ephems(source, ant_list)
        else: 
            # we got a "none" source to track, so let's get the source the 
            # antennas are currently observing
            sources = list(ata_control.get_eph_source(ant_list).values())
            # this will get the source for all the allocated antennas
            sources_unique = list(set(sources))

            if len(sources) == 1:
                # all antennas are pointed at the same place
                source = sources_unique[0]
            else:
                # Why are we using the beamformer if not all antennas are
                # pointed at the same source??
                # I'll get the most common source the antennas are pointed at
                source = most_common(sources)



        obstime = float(self.config['ObsTime'])
        hp_targets = self.config['hp_targets']

        if obstime != 0:
            current_backend = get_current_backend(hp_targets)

            # If beamformer, let's configure the beams
            if 'BLADE' in current_backend.upper():
                try:
                    ra, dec = ata_control.get_source_ra_dec(source)
                except ATARestException as e:
                    # source not in database...?
                    # just get the ra, dec from first antenna
                    ra, dec = ata_control.get_ra_dec(ant_list[0])[ant_list[0]]

                # First populate the central beam
                # Note: these can be overwritten if user passes 
                # "RA_OFF0" and "DEC_OFF0"
                keyval_dict = {'RA_OFF0': ra, 'DEC_OFF0': dec}

                # cluncky way to do things, but I want to search for the 
                # number of beams, so I assume if RA_OFF is present, 
                # it means we have a beam on sky
                beams = []
                for key in self.config.keys():
                    if 'RA_OFF' in key.upper():
                        beams.append(key.replace("RA_OFF", "")) # I am collecting beam numbers

                for beam in beams:
                    # Make sure both RA_OFFX and DEC_OFFX exist for X beam
                    if f"DEC_OFF{beam}" not in self.config:
                        self.write_status(f"DEC_OFF{beam} does not exist!")
                    else:
                        keyval_dict[f"RA_OFF{beam}"] = self.config[f"RA_OFF{beam}"]
                        keyval_dict[f"DEC_OFF{beam}"] = self.config[f"DEC_OFF{beam}"]

                hpguppi_auxillary.publish_keyval_dict_to_redis(keyval_dict,
                        hp_targets, postproc=False)

            elif "XGPU" in current_backend.upper():
                # set integration time if provided
                if "XTIMEINT" in self.config:
                    keyval_dict = {'XTIMEINT': self.config['XTIMEINT']}
                    hpguppi_auxillary.publish_keyval_dict_to_redis(keyval_dict,
                            hp_targets, postproc=False)

            obs_start_in = 10
            hpguppi_record_in.record_in(obs_start_in, obstime,
                    hashpipe_targets = hp_targets)
            self.write_status(f"Recording for {obstime}")
            time.sleep(obstime + obs_start_in + 5)




class ScheduleExecutor:
    def __init__(self, action_type, config, write_status=print):
        self.executor = self._get_executor(action_type, config, write_status)

    def _get_executor(self, action_type, config, write_status):
        if action_type == "SETFREQ":
            return SetFreqTunning(config, write_status)
        elif action_type == "BACKEND":
            return SetBackend(config, write_status)
        elif action_type == "TRACK":
            return TrackAndObserve(config, write_status)
        elif action_type == "WAIT":
            return None

    def execute(self):
        self.executor.execute()
