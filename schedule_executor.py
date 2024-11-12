import os,sys
from parse import parse
from abc import ABC, abstractmethod
import time

from ATATools import ata_control, logger_defaults, ata_if


PROJECTID_FNAME = "./projects.json"
BACKENDS_FNAME = "./backends.json"
POSTPROCESSORS_FNAME = "./postprocessors.json"


def load_project_id_json(projectid_fname=PROJECTID_FNAME):
    with open(projectid_fname, 'r') as json_file:
        self.projectid_mapping = json.load(json_file)

def load_backends_json(backends_fname=BACKENDS_FNAME):
    with open(backends_fname, 'r') as json_file:
        self.backends_mapping = json.load(json_file)

def load_postprocessors_json(postprocessors_fname=POSTPROCESSORS_FNAME):
    with open(postprocessors_fname, 'r') as json_file:
        self.postprocessors_mapping = json.load(json_file)


class Executable(ABC):
    def __init__(self, config):
        self.config = config

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
    def __init__(self):
        needed_keys = ["ant_list", "rf_gain", "if_gain",
                       "eq_level", "tuningA", "tuningB",
                       "tuningC", "tuningD"]
        self.check_consistency(needed_keys)
    def execute(self):
        # Get all the needed LOs
        los   = []
        freqs = []
        for t in ["a", "b", "c", "d"]: 
            t_config = 'tuning'+t.upper()
            if self.config[t_config]:
                los.append(t)
                freqs = self.config[t_config]
        if los:
            self.write_status("Setting frequencies for LOs: %s" %los)
            max_freq = max(freqs)
            lo_max_freq = lo[freqs.index(max_freq)]

            for lo, freq in zip(los, freqs):
                if lo == max_freq:
                    ata_control.set_freq(freq, self.config['ant_list'], 
                                         lo=lo)
                else:
                    ata_control.set_freq(freq, self.config['ant_list'], 
                                         lo=lo, nofocus=True)
            time.sleep(20)

        if self.config['rf_gain']:
            self.write_status("Tunning RF:")
            ata_control.autotune(self.config['ant_list'])

        if self.config['if_gain']:
            self.write_status("Tuning IF:")
            ata_if.tune_if(self.config['ant_list'], los=los)

        if self.config['eq_level']:
            pass


class SetBackend(Executable):
    def __init__(self):
        needed_keys = ["ant_list", "ProjectID", 
                "Backend", "Postprocessor"]
        self.check_consistency(needed_keys)

    def execute(self):
        projectid_mapping      = load_project_id_json()
        backends_mapping       = load_backends_json()
        postprocessors_mapping = load_postprocessors_json()

        # Set backend
        

class ScheduleExecutor:
    def __init__(self, action_type, config, write_status=None):
        self.executor = self._get_executor(action_type, config)
        if write_status:
            self.write_status = write_status
        else:
            self.write_status = print

    def _get_executor(self, action_type, config):
        if action_type == "SET FREQ":
            return SetFreqTunning(config)
        elif action_type == "BACKEND":
            return SetBackend(config)

    def execute(self):
        self.executor.execute()
