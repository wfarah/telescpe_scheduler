import tkinter as tk
#import mttkinter.mtTkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox, font
from tkinter.scrolledtext import ScrolledText

from tkcalendar import DateEntry
from PIL import Image, ImageTk
import json
import time
import threading, multiprocessing, traceback
import queue

import argparse
import logging

from schedule_executor import ScheduleExecutor
from ata_obs_plan import ObsPlan #from ATATools.ata_obs_plan import ObsPlan
from ata_obs_plot_app import ObsPlotApp #from ATATools.ata_obs_plot_app import ObsPlotApp
import ATATools.ata_sources as check

from odsutils import ods_engine

from astropy.time import Time, TimeDelta

import datetime
from datetime import timezone
import pytz
from parse import parse

from SNAPobs import snap_config
from SNAPobs.snap_hpguppi import snap_hpguppi_defaults as hpguppi_defaults

import os
import tempfile

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


DEFAULT_TZ = "US/Pacific"
PROJECTID_FNAME = "./projects.json"
BACKENDS_FNAME = "./backends.json"
POSTPROCESSORS_FNAME = "./postprocessors.json"

WAIT_FOR_PROMPT_DEFAULT = 600 # assume 10 minutes


TITLE_FONT = ("Helvetica", 18)
NORMAL_FONT = ("Helvetica", 14)
TEXTBOX_FONT = ("Monospace", 12, "bold")
FILL_FONT = ("Helvetica", 12)

LOGGING_DTFMT = "%Y-%m-%d %H:%M:%S.%f"
LOGGING_INFO_COLOR = ["green"]
LOGGING_WARNING_COLOR = ["orange", "dark orange"]
LOGGING_ERROR_COLOR = ["red", "dark red"]

WAIT_DTFMT = "%Y-%m-%dT%Hh%Mm%Ss%z"

ODS_DEFAULTS = "/opt/mnt/share/ods_defaults.json"
ODS_WRITE    = "/home/sonata/ods.json"
ODS_WRITE    = "/opt/mnt/share/ods_upload/ods.json"

def is_positive_number(s):
    try:
        ss = float(s)
        if ss < 0:
            return False
        return True
    except ValueError:
        return False

def hashpipe_targets_to_list(hp_targets):
    hp_list = []
    for key in hp_targets.keys():
        hp_list += [key + "." + str(i) for i in hp_targets[key]]
    return hp_list

def list_to_hashpipe_targets(hp_list):
    hp_targets = {}
    for i in hp_list:
        seti_node, instance = i.split(".")
        if seti_node in hp_targets.keys():
            hp_targets[seti_node].append(int(instance))
        else:
            hp_targets[seti_node] = [int(instance)]

    return hp_targets


def send_slack_message(token, channel, text):
    """
    Function to send a text message to a slack channel using an auth token
    """
    client = WebClient(token=token)

    try:
        response = client.chat_postMessage(
            channel=channel,
            text=text
        )
    except SlackApiError as e:
        # Handle the exception if there was an error
        raise e

class ObsPlotAppSecondary(tk.Toplevel):
    def __init__(self, parent, obs):
        super().__init__(parent)

        self.geometry("1550x900")
        self.app = ObsPlotApp(self)
        self.app.load_from_obsplan(obs)



class ExceptionThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.exception = None

    def run(self):
        try:
            if self._target:
                self._target(*self._args, **self._kwargs)
        except Exception as e:
            self.exception = e

class ExceptionProcess(multiprocessing.Process):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pconn, self._cconn = multiprocessing.Pipe()
        self._exception = None

    def run(self):
        try:
            multiprocessing.Process.run(self)
            self._cconn.send(None)
        except Exception as e:
            self._cconn.send(e)
            #raise e # can also raise it here

    @property
    def exception(self):
        if self._pconn.poll():
            self._exception = self._pconn.recv()
        return self._exception


class DropdownWithCheckboxes(tk.Frame):
    def __init__(self, parent, options, text="Select options",
                 bg="lightgrey", width=80, height=150):
        super().__init__(parent)
        
        # Initialize options and create BooleanVars to track each checkbox's state
        self.options = options
        self.vars = {}
        
        # Button to toggle the dropdown menu
        self.button = tk.Button(self, text=text, command=self.toggle_menu,
                                font=NORMAL_FONT, bg=bg)
        self.button.pack(fill="x")

        # Toplevel window for the dropdown menu, initially hidden
        self.menu_window = None
        self.root = parent  # Store the parent to bind/unbind global events

        self.height = height
        self.width  = width

        # Add initial checkboxes
        self.create_menu()

    def create_menu(self):
        # Create Toplevel menu window with checkboxes
        self.menu_window = tk.Toplevel(self)
        self.menu_window.withdraw()  # Start hidden
        self.menu_window.overrideredirect(True)  # Remove window decorations

        # Frame to hold the dropdown checkboxes
        self.checkbox_frame = tk.Frame(self.menu_window)
        self.checkbox_frame.pack(fill="both", expand=True)

        # Canvas for scrolling
        self.canvas = tk.Canvas(self.checkbox_frame, height=self.height, width=self.width,
                        bd=4, relief=tk.RIDGE)  # Set fixed size for scrollable area
        self.canvas.pack(side="left", fill="both", expand=True)

        # Scrollbar for the canvas
        self.scrollbar = tk.Scrollbar(self.checkbox_frame, orient="vertical", command=self.canvas.yview)
        self.scrollbar.pack(side="right", fill="y")

        # Configure canvas scrolling
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        # Frame inside the canvas to hold the checkboxes
        self.inner_frame = tk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")

        # Populate checkboxes
        self.update_options(self.options)

    def update_options(self, options):
        # Clear existing checkboxes and variables
        for widget in self.inner_frame.winfo_children():
            widget.destroy()
        self.vars.clear()

        # Add new options and their checkboxes
        self.options = options
        for option in self.options:
            var = tk.BooleanVar(value=True)
            self.vars[option] = var
            chk = tk.Checkbutton(self.inner_frame, text=option, variable=var,
                    font=NORMAL_FONT)
            chk.pack(anchor="w", padx=5)

        # Update the scroll region to fit content
        self.update_scrollregion()


    def update_scrollregion(self):
        # Update the scrollable region to encompass all checkboxes
        self.inner_frame.update_idletasks()
        self.canvas.config(scrollregion=self.canvas.bbox("all"))

    def toggle_menu(self):
        # Show or hide the dropdown menu above the button
        if self.menu_window.winfo_viewable():
            self.hide_menu()
        else:
            # Position the dropdown menu just above the button
            x = self.button.winfo_rootx()
            y = self.button.winfo_rooty() + self.button.winfo_height()  # Position below the button
            self.menu_window.geometry(f"+{x}+{y}")
            self.menu_window.deiconify()
            self.update_scrollregion()

            # Bind a global click event to close the dropdown if clicked outside
            self.root.bind_all("<Button-1>", self.click_outside)
            self.root.bind_all("<FocusOut>", self.hide_menu)
            # Bind Escape key to root to close the dropdown
            self.root.bind_all("<Escape>", lambda e: self.hide_menu())

    def hide_menu(self, event=None):
        # Hide the dropdown menu and unbind the click event
        self.menu_window.withdraw()
        self.root.unbind_all("<Button-1>")  # Unbind click event when hiding menu
        self.root.unbind_all("<Escape>")    # Unbind Escape key when hiding menu
        self.root.unbind_all("<FocusOut>")

    def click_outside(self, event):
        # Check if the click was outside the dropdown menu
        if not (self.menu_window.winfo_rootx() <= event.x_root <= self.menu_window.winfo_rootx() + self.menu_window.winfo_width() and
                self.menu_window.winfo_rooty() - self.button.winfo_height() <= event.y_root <= self.menu_window.winfo_rooty() + self.menu_window.winfo_height()):
            self.hide_menu()

    def get_selected_options(self):
        # Return a list of selected options
        return [option for option, var in self.vars.items() if var.get()]


class LogWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Log Window")
        self.geometry("1000x900")

        # Add a scrolled text widget to display logs
        self.log_text = ScrolledText(self, state="disabled", wrap="word", 
                font=TEXTBOX_FONT)
        self.log_text.pack(expand=True, fill="both", padx=10, pady=10)

    def add_log(self, message, color="green"):
        """
        Add a log message with a specified color.

        Args:
            message (str): The log message to add.
            color (str): The color of the log message (e.g., "red", "blue", "#RRGGBB").
        """
        # Create a unique tag for the color
        tag_name = f"tag_{color}"
        if not tag_name in self.log_text.tag_names():
            self.log_text.tag_configure(tag_name, foreground=color)

        # Insert the log message with the color tag
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n", tag_name)
        self.log_text.configure(state="disabled")
        self.log_text.see("end")  # Automatically scroll to the end

class SourceWidget(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Source Checker")
        self.geometry("950x300")
        self.parent = parent

        # Input field for source
        ttk.Label(self, text="Enter Source:", font=NORMAL_FONT).pack(pady=10)
        self.source_input = ttk.Entry(self, font=NORMAL_FONT)
        self.source_input.pack(pady=5)

        # Check Source Button
        self.check_button = tk.Button(self, text="Check Source", 
                command=self.check_source, font=NORMAL_FONT)
        self.check_button.pack(pady=10)

        # Display Area
        self.output_text = tk.Text(self, wrap="word", height=8, width=80, 
                font=TEXTBOX_FONT)
        self.output_text.pack(pady=10)
        self.output_text.insert("1.0", "Source details will appear here.\n")
        self.output_text.config(state="disabled")  # Make it read-only initially

    def check_source(self):
        # Get the input source
        source_name = self.source_input.get().strip()

        # Check the source and print the dictionary values
        self.output_text.config(state="normal")  # Allow editing temporarily
        self.output_text.delete("1.0", tk.END)  # Clear existing text

        dt = datetime.datetime.now(
                tz=pytz.timezone(DEFAULT_TZ))
        try:
            source_info = check.check_source_str(dt, sourcename=source_name)
            source_info = source_info.replace("\n", "\n ")
            self.output_text.insert("1.0", source_info)
        except Exception as e:
            #self.parent.write_status("check source failed", fg='red')
            #self.parent.write_status(e.args[0], fg='red')
            text = f"Source '{source_name}' not found.\n"
            text += "Error:\n"
            text += str(e.args[0]) + "\n"
            self.output_text.insert("1.0", text)

        
        #if source_name == source_data['object']:
        #    output = "\n".join(f"{key}: {value}" for key, value in source_data.items())
        #    self.output_text.insert("1.0", f"Details for source '{source_name}':\n{output}\n")
        #else:
        #    self.output_text.insert("1.0", f"Source '{source_name}' not found.\n")
        
        self.output_text.config(state="disabled")  # Make it read-only again



class TelescopeSchedulerApp(tk.Tk):
    def __init__(self, args):
        #self.root = root
        super().__init__()
        self.title("Allen Telescope Array Scheduler")

        # Set window size to 1200x900
        self.geometry("1700x900")
        self.pipe_conn = None # multiprocess pipe to pass interrupt requests
        self.to_enable_disable = [] #list of everything to enable and disable
        self.to_readonly_disable = [] # same as above, but return to readonly

        self.original_listbox = ()

        # load project IDs
        self.load_project_id_json()
        self.load_backends_json()
        self.load_postprocessors_json()

        self.debug = args.debug
        self.ignore_check_schedule = args.ignore_check

        if self.debug:
            self.enable_slack = False
        else:
            self.enable_slack = True

        # Configure the root grid layout to have two columns
        #self.root.grid_columnconfigure(0, weight=1)  # Left frame
        #self.root.grid_columnconfigure(1, weight=1)  # Right frame

        # Create the left and right frames
        self.frame_left = tk.Frame(self)
        self.frame_right = tk.Frame(self)

        self.frame_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.frame_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # Place the frames in a grid (equally split)
        #self.frame_left.grid(row=0, column=0, sticky="nsew")
        #self.frame_right.grid(row=0, column=1, sticky="nsew")

        # Configure the grid to expand both frames equally
        self.frame_left.grid_rowconfigure(0, weight=1)
        self.frame_right.grid_rowconfigure(0, weight=1)

        self.frame_left.grid_columnconfigure(0, weight=1)
        self.frame_right.grid_columnconfigure(0, weight=1)


        # Load the PNG image using Pillow
        #self.image = Image.open("/Users/wael/Downloads/ATA_image.png")
        #r = 2288 / 1266
        #w = 600
        #h = int(w / r)
        #self.image = self.image.resize((w, h), Image.Resampling.LANCZOS)
        #self.tk_image = ImageTk.PhotoImage(self.image)

        # Display the image in a Label
        #self.image_label = tk.Label(self.frame_left, image=self.tk_image)
        #self.image_label.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)

        # Left-hand side (listbox)
        self.listbox = tk.Listbox(self.frame_left, width=75, selectmode=tk.MULTIPLE,
                                  bd=2, relief=tk.RAISED, font=("Helvetica", 14))
        self.listbox.grid(row=0, column=1, sticky="nsew", padx=5, pady=9)

        # Add a frame to hold the move buttons next to the listbox
        move_button_frame = tk.Frame(self.frame_left, width=5)
        move_button_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=0)  # Buttons will be on the right, aligned vertically

        # Up and down buttons for moving the selection in the listbox
        select_up_button = tk.Button(move_button_frame, text="⇑", command=self.move_entry_up, width=1, height=1)  # Tiny button
        self.to_enable_disable.append(select_up_button)
        select_up_button.pack(padx=5, pady=10)

        # Up and down buttons for moving the entries in the listbox
        move_up_button = tk.Button(move_button_frame, text="↑", command=self.move_selection_up, width=1, height=1)  # Tiny button
        move_up_button.pack(padx=5, pady=10)

        move_down_button = tk.Button(move_button_frame, text="↓", command=self.move_selection_down, width=1, height=1)  # Tiny button
        move_down_button.pack(padx=5, pady=10)
        self.to_enable_disable.append(move_up_button)
        self.to_enable_disable.append(move_down_button)


        select_down_button = tk.Button(move_button_frame, text="⇓", command=self.move_entry_down, width=0, height=0)  # Tiny button
        select_down_button.pack(padx=5, pady=10)
        self.to_enable_disable.append(select_down_button)

        # Add a frame to hold the buttons below the listbox
        listbox_button_frame = tk.Frame(self.frame_left, bd=2, relief=tk.SUNKEN)
        #listbox_button_frame.pack(fill=tk.X, padx=10, pady=10)
        listbox_button_frame.grid(row=2, column=1, sticky="nsew", padx=5, pady=6)

        # Add the "Duplicate Entry" button
        duplicate_button = tk.Button(listbox_button_frame, text="Duplicate Entries", 
                command=self.duplicate_entry, font=NORMAL_FONT)
        duplicate_button.pack(side=tk.LEFT, padx=10, pady=12, fill=tk.BOTH)
        self.to_enable_disable.append(duplicate_button)

        # Add the "Delete Entry" button
        delete_button = tk.Button(listbox_button_frame, text="Delete Entries", 
                command=self.delete_entry, font=NORMAL_FONT)
        delete_button.pack(side=tk.RIGHT, padx=0)
        self.to_enable_disable.append(delete_button)

        # Error display label
        self.obs_status = tk.Label(self.frame_left, text="", font=('Helvetica', 16),
                width = 75)
        self.obs_status.grid(row=4, column=1, padx=10, pady=10)
        #self.obs_status.config(text="Bla, bla")

        # Create a progress bar in indeterminate mode
        #self.progress_var = tk.DoubleVar()
        #self.progress_bar = ttk.Progressbar(self.frame_left, variable=self.progress_var, length=400, maximum=100)
        #self.progress_bar.grid(row=3, column=1, padx=10, pady=10)
        #progress_bar.start()

        # Bind the "Q" key to delete the selected entry
        self.bind("<d>", self.delete_entry)

        # Bind the "BackSpace" key to delete the selected entry
        self.bind("<BackSpace>", self.delete_entry)

        # Bind the "Escape" key to reset the selection
        self.bind("<Escape>", self.reset_selection)

        # WF: removed all dragging stuff, will replace by buttons
        # Initialize dragging variables
        #self.dragging = False
        #self.dragged_index = None

        # Bind mouse events for dragging
        #self.listbox.bind("<Button-1>", self.on_click)
        #self.listbox.bind("<B1-Motion>", self.on_drag)
        #self.listbox.bind("<ButtonRelease-1>", self.on_release)

        # Right-hand side (Divided into 4 parts)
        self.setup_right_frame()

        # Create a menu bar
        self.menu_bar = tk.Menu(self)
        self.config(menu=self.menu_bar)

        # Add File menu to the menubar 
        file_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="File", menu=file_menu, 
                font=NORMAL_FONT)
        file_menu.add_command(label="New", command=self.new_schedule, 
                font=NORMAL_FONT)
        file_menu.add_command(label="Open", command=self.open_schedule, 
                font=NORMAL_FONT)
        file_menu.add_command(label="Save", command=self.save_schedule, 
                font=NORMAL_FONT)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.check_if_modified_and_quit, 
                font=NORMAL_FONT)

        # Add Source menu to the menubar
        source_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Source", menu=source_menu, 
                font=NORMAL_FONT)
        source_menu.add_command(label="Check source", 
                command=self.open_check_source, font=NORMAL_FONT)

        # Add Help menu to the menubar
        log_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Log", menu=log_menu, 
                font=NORMAL_FONT)
        log_menu.add_command(label="Show log", 
                command=self.open_log_window, font=NORMAL_FONT)


        # Add Help menu to the menubar
        help_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Help", menu=help_menu, 
                font=NORMAL_FONT)
        help_menu.add_command(label="How to Use", command=self.show_help, 
                font=NORMAL_FONT)
        help_menu.add_separator()
        help_menu.add_command(label="About", font=NORMAL_FONT)

        # check if schedule is modified before exiting
        self.protocol("WM_DELETE_WINDOW", self.check_if_modified_and_quit)
        # Add log
        self.log_window = LogWindow(self)

        logging.basicConfig(
            level=logging.INFO,  # Set the minimum logging level
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',  # Log format
            handlers=[
                logging.FileHandler("app.log"),  # Log to a file
                logging.StreamHandler()         # Log to the console
            ]
        )

        self.logger = logging.getLogger("ATAObsSchedulerLogger")

        # Create a queue for thread communication to the main GUI update method
        self.task_queue = multiprocessing.Queue() #queue.Queue()

        # Now start it
        self.after(100, self.gui_process_queue)

        if self.debug:
            self.write_status("Running scheduler in debug mode")

    def setup_right_frame(self):
        # Create 4 sub-frames within the right frame
        self.antenna_observer_frame = tk.Frame(self.frame_right)
        self.backend_frame = tk.Frame(self.frame_right, bd=2, relief=tk.SUNKEN)
        self.frequency_frame = tk.Frame(self.frame_right, bd=2, relief=tk.SUNKEN)
        self.source_frame = tk.Frame(self.frame_right)
        self.button_frame = tk.Frame(self.frame_right, bd=2, relief=tk.SUNKEN)

        # Pack the frames with some padding
        self.antenna_observer_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.backend_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.frequency_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.source_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.button_frame.pack(fill=tk.X, padx=10, pady=10)

        self.antenna_frame = tk.Frame(self.antenna_observer_frame, bd=2, relief=tk.SUNKEN)
        self.antenna_frame.pack(fill='both', expand=True, side=tk.LEFT, pady=2)

        antenna_label = tk.Label(self.antenna_frame, text="Select Antennas & Recorders", font=TITLE_FONT)
        antenna_label.pack(pady=2)

        antenna_inner_frame = tk.Frame(self.antenna_frame)
        antenna_inner_frame.pack(fill=tk.X, padx=10, pady=5)

        self.observer_frame = tk.Frame(self.antenna_observer_frame, bd=2, relief=tk.SUNKEN)
        self.observer_frame.pack(pady=5, padx=5, fill=tk.Y, expand=True)

        oic_frame = tk.Frame(self.observer_frame)
        oic_frame.pack(pady=5, padx=5)
        observer_label = tk.Label(oic_frame, text="Observer in Charge:",
                font=NORMAL_FONT)
        observer_label.pack(side=tk.LEFT, pady=5)

        self.observer = tk.Entry(oic_frame, font=NORMAL_FONT, width=20)
        self.observer.pack(pady=5)
        self.to_enable_disable.append(self.observer)

        self.oic_frame = tk.Frame(self.observer_frame)
        self.oic_frame.pack(fill=tk.X, padx=10, pady=5)

        self.register_oic_button = tk.Button(self.observer_frame,
                text="Register as OIC", bg="lightblue", font=NORMAL_FONT,
                command=self.register_oic)
        self.register_oic_button.pack(side=tk.LEFT, padx=5, pady=5)
        self.registered_observer = ""

        self.deregister_oic_button = tk.Button(self.observer_frame,
                text="Deregister", bg="orange", font=NORMAL_FONT,
                command=self.deregister_oic)
        self.deregister_oic_button.pack(side=tk.RIGHT, padx=5, pady=5)
        self.deregister_oic()
        self.to_enable_disable.append(self.deregister_oic_button)


        options = ["tmp1", "tmp2"]
        self.antenna_dropdown = DropdownWithCheckboxes(antenna_inner_frame, options,
                                          text="Antennas", bg="lightblue")
        self.antenna_dropdown.pack(side=tk.LEFT, padx=5, pady=5)
        self.to_enable_disable.append(self.antenna_dropdown.button)

        self.targets_dropdown = DropdownWithCheckboxes(antenna_inner_frame, options,
                                          text="Recorders", bg="lightblue", width=150)
        self.targets_dropdown.pack(side=tk.LEFT, padx=5, pady=5)
        self.to_enable_disable.append(self.targets_dropdown.button)

        self.antenna_button = tk.Button(antenna_inner_frame, 
                text="Refresh", font=NORMAL_FONT, bg="lightblue",
                command=self.refresh_ant_targets)
        self.refresh_ant_targets()

        self.antenna_button.pack(padx=5, pady=5)
        self.to_enable_disable.append(self.antenna_button)


        backend_label = tk.Label(self.backend_frame, text="Select Digitizer mode", font=TITLE_FONT)
        backend_label.pack(pady=10)

        dropdown_frame_digitizer_mode = tk.Frame(self.backend_frame)
        dropdown_frame_digitizer_mode.pack(fill=tk.X, padx=5, pady=5)

        digitizer_mode_label = tk.Label(dropdown_frame_digitizer_mode, 
                                        text="Digitizer mode:",
                                        font=NORMAL_FONT)
        digitizer_mode_label.pack(side=tk.LEFT, padx=5)
        digitizer_mode_options = ["Continuum mode", "Spectral line mode (16MHz)"]

        self.digitizer_mode_dropdown = ttk.Combobox(dropdown_frame_digitizer_mode,
                                        values=digitizer_mode_options, 
                                        width=20, state = 'readonly',
                                        font=FILL_FONT)  # Adjusted width
        self.digitizer_mode_dropdown.pack(side=tk.LEFT, padx=5)
        self.to_readonly_disable.append(self.digitizer_mode_dropdown)

        # Button to add digitizer mode setup
        add_digitizer_mode_button = tk.Button(dropdown_frame_digitizer_mode, 
                                text="Add Digitizer mode", 
                                command=self.add_digitizer_mode,
                                font=NORMAL_FONT,
                                bg="lightblue")
        add_digitizer_mode_button.pack(pady=10)
        self.to_enable_disable.append(add_digitizer_mode_button)



        backend_label = tk.Label(self.backend_frame, text="Select Backend and Postprocessor", font=TITLE_FONT)
        backend_label.pack(pady=10)

        # Frame to hold backend and postprocessor dropdowns side by side
        dropdown_frame = tk.Frame(self.backend_frame)
        dropdown_frame.pack(fill=tk.X, padx=5, pady=5)

        # ProjectID Label and Dropdown Menu
        projectid_label = tk.Label(dropdown_frame, text="Project ID:",
                                   font=NORMAL_FONT)
        projectid_label.pack(side=tk.LEFT, padx=5)
        projectid_options = list(self.projectid_mapping.keys())
        self.projectid_dropdown = ttk.Combobox(dropdown_frame, values=projectid_options, width=5,
                state = 'readonly',
                font=FILL_FONT)  # Adjusted width
        self.projectid_dropdown.pack(side=tk.LEFT, padx=5)
        self.to_readonly_disable.append(self.projectid_dropdown)
        self.projectid_dropdown.bind('<<ComboboxSelected>>', self.update_backend_combobox)

        # Backend Dropdown Menu
        backend_label = tk.Label(dropdown_frame, text="Backend:",
                                 font=NORMAL_FONT)
        backend_label.pack(side=tk.LEFT, padx=5)
        backend_options = []
        self.backend_dropdown = ttk.Combobox(dropdown_frame, values=backend_options,
                width=15, state = 'readonly',
                font=FILL_FONT)  # Adjusted width
        self.backend_dropdown.pack(side=tk.LEFT, padx=5)
        self.to_readonly_disable.append(self.backend_dropdown)
        self.backend_dropdown.bind('<<ComboboxSelected>>', self.update_postprocessor_combobox)

        # Postprocessor Dropdown Menu
        postprocessor_label = tk.Label(dropdown_frame, text="Postprocessor:", font=NORMAL_FONT)
        postprocessor_label.pack(side=tk.LEFT, padx=5)
        postprocessor_options = []
        self.postprocessor_dropdown = ttk.Combobox(dropdown_frame, values=postprocessor_options,
                width=15, state = 'readonly',
                font=FILL_FONT)  # Adjusted width
        self.postprocessor_dropdown.pack(side=tk.LEFT, padx=5)
        self.to_readonly_disable.append(self.postprocessor_dropdown)

        # Frame for integration length
        #int_length_frame = tk.Frame(self.backend_frame)
        #int_length_frame.pack(fill=tk.X, padx=5, pady=5)

        #integration_lengths_label = tk.Label(int_length_frame, text="Integration length:")
        #integration_lengths_label.pack(side=tk.LEFT, padx=5)
        #self.int_length_entry = tk.Entry(int_length_frame, width=10)
        #self.int_length_entry.pack(side=tk.LEFT, padx=5)
        #self.to_enable_disable.append(self.int_length_entry)
        #self.int_length_entry.config(state=tk.DISABLED)

        # Button to add backend setup
        add_backend_button = tk.Button(self.backend_frame, text="Add Backend Setup",
                command=self.add_backend_setup, font=NORMAL_FONT, bg="lightblue")
        add_backend_button.pack(pady=10)
        self.to_enable_disable.append(add_backend_button)

        # Frequency frame with tuning inputs and checkboxes
        self.setup_frequency_frame()

        source_frame_left = tk.Frame(self.source_frame, bd=2, relief=tk.SUNKEN)
        source_frame_left.pack(fill="both", expand=True, side=tk.LEFT, pady=2)

        source_frame_right = tk.Frame(self.source_frame, bd=2, relief=tk.SUNKEN)
        source_frame_right.pack(fill='both', expand=True, side=tk.LEFT, pady=2)

        # Source frame - Placeholder
        source_label = tk.Label(source_frame_left, text="Source Name and Observation Times", font=TITLE_FONT)
        source_label.pack(pady=10)

        # Create a new frame to hold the labels and text boxes side by side
        source_frame_inner = tk.Frame(source_frame_left)
        source_frame_inner.pack(fill=tk.X, padx=10, pady=10)

        # Add "Source Name" label and text box
        source_name_label = tk.Label(source_frame_inner, text="Source:", font=NORMAL_FONT)
        source_name_label.pack(side=tk.LEFT, padx=5)
        self.source_name_entry = tk.Entry(source_frame_inner, width=12, font=NORMAL_FONT)
        self.source_name_entry.pack(side=tk.LEFT, padx=5)
        self.to_enable_disable.append(self.source_name_entry)

        # Add "Observation Time" label and text box
        obs_time_label = tk.Label(source_frame_inner, text="Obs Time:",
                                  font=NORMAL_FONT)
        obs_time_label.pack(side=tk.LEFT, padx=5)
        self.obs_time_entry = tk.Entry(source_frame_inner, width=6,
                                       font=NORMAL_FONT)
        self.obs_time_entry.pack(side=tk.LEFT, padx=5)
        self.to_enable_disable.append(self.obs_time_entry)

        source_frame_inner2 = tk.Frame(source_frame_left)
        source_frame_inner2.pack(fill=tk.X, padx=10, pady=10)

        # Add button to add source and observation time to the listbox
        add_source_button = tk.Button(source_frame_inner2, text="Add Source", 
                command=self.add_source_entry, font=NORMAL_FONT, bg="lightblue")
        self.to_enable_disable.append(add_source_button)
        add_source_button.pack(side=tk.LEFT, padx=10)

        park_antenna_button = tk.Button(source_frame_inner2, text="Park Antennas",
                                command=self.add_park_command,
                                        font=NORMAL_FONT,
                                        bg="lightblue")
        self.to_enable_disable.append(park_antenna_button)
        park_antenna_button.pack(side=tk.RIGHT, padx=5)

        wait_frame_1 = tk.Frame(source_frame_right)
        wait_frame_1.pack(fill=tk.X, padx=2, pady=5)

        wait_frame_2 = tk.Frame(source_frame_right)
        wait_frame_2.pack(fill=tk.X, padx=2, pady=5)

        wait_frame_3 = tk.Frame(source_frame_right)
        wait_frame_3.pack(fill=tk.X, padx=2, pady=5)

        wait_frame_4 = tk.Frame(source_frame_right)
        wait_frame_4.pack(fill=tk.X, padx=2, pady=5)

        self.wait_time_button = tk.Button(wait_frame_1, text="Wait until", 
                font=NORMAL_FONT, command=self.wait_until,
                bg="lightblue")
        self.wait_time_button.pack(side=tk.LEFT, padx=5)
        self.to_enable_disable.append(self.wait_time_button)

        wait_frame_1_dt = tk.Frame(wait_frame_1)
        wait_frame_1_dt.pack(fill=tk.X, padx=2, pady=2)

        self.tz_dropdown = ttk.Combobox(wait_frame_1_dt, values=pytz.all_timezones,
                font=NORMAL_FONT, state="readonly", width=16)
        self.to_readonly_disable.append(self.tz_dropdown)

        self.tz_dropdown.pack(fill=tk.X, padx=2, pady=2)
        self.tz_dropdown.option_add('*TCombobox*Listbox.font', NORMAL_FONT)
        self.tz_dropdown.set(DEFAULT_TZ)

        # Get time now to fill in defaults
        dt_now = datetime.datetime.now(
                tz=pytz.timezone(self.tz_dropdown.get()))
        hh_now, mm_now, ss_now = dt_now.hour, dt_now.minute, dt_now.second

        self.date_entry = DateEntry(wait_frame_2, width=8, background='darkblue',
                        foreground='white', borderwidth=2, font=NORMAL_FONT)
        self.date_entry.pack(side=tk.LEFT, pady=2, padx=5)
        self.to_enable_disable.append(self.date_entry)

        self.hours_spin = tk.Spinbox(wait_frame_2, from_=2, to=23, width=3, increment=1,
            format="%02.0f", font=NORMAL_FONT, textvariable=tk.IntVar(value=hh_now))
        self.minutes_spin = tk.Spinbox(wait_frame_2, from_=0, to=59, width=3, increment=1,
            format="%02.0f", font=NORMAL_FONT, textvariable=tk.IntVar(value=mm_now))
        self.seconds_spin = tk.Spinbox(wait_frame_2, from_=0, to=59, width=3, increment=1,
            format="%02.0f", font=NORMAL_FONT, textvariable=tk.IntVar(value=ss_now))
        self.hours_spin.pack(side=tk.LEFT, padx=5)
        self.minutes_spin.pack(side=tk.LEFT)
        self.seconds_spin.pack(side=tk.LEFT)
        self.to_enable_disable.append(self.hours_spin)
        self.to_enable_disable.append(self.minutes_spin)
        self.to_enable_disable.append(self.seconds_spin)

        self.reset_time_button = tk.Button(wait_frame_2, font=FILL_FONT, text="now",
                command=self.reset_time)
        self.reset_time_button.pack(side=tk.RIGHT, pady=2, padx=5)
        self.reset_time()
        self.to_enable_disable.append(self.reset_time_button)

        self.wait_until_button = tk.Button(wait_frame_3, text="Wait for prompt", 
                font=NORMAL_FONT, command=self.wait_for_prompt,
                bg="lightblue", width=12)
        self.wait_until_button.pack(side=tk.LEFT, padx=5)
        self.to_enable_disable.append(self.wait_until_button)

        self.wait_for_button = tk.Button(wait_frame_4, text="Wait for:", 
                font=NORMAL_FONT, command=self.wait_for_seconds,
                bg="lightblue")
        self.wait_for_button.pack(side=tk.LEFT, padx=5)
        self.wait_for_entry = tk.Entry(wait_frame_4, font=NORMAL_FONT, width=5)
        self.wait_for_entry.pack(side=tk.LEFT, padx=5)
        sec_lab = tk.Label(wait_frame_4, text="sec", font=NORMAL_FONT)
        sec_lab.pack(side=tk.LEFT, padx=3)
        self.to_enable_disable.append(self.wait_for_entry)
        self.to_enable_disable.append(self.wait_for_button)

        # Button frame - Two buttons
        check_button = tk.Button(self.button_frame, text="Check Schedule", width=15,
                                 font=NORMAL_FONT, command=self.check_schedule, bg="orange")
        self.to_enable_disable.append(check_button)
        self.execute_button = tk.Button(self.button_frame, text="Execute Schedule", width=15,
                                   font=NORMAL_FONT, command=self.execute_schedule, bg="lightgreen")
        self.to_enable_disable.append(self.execute_button)
        self.disable_execute()
        abort_button = tk.Button(self.button_frame, text="Abort Schedule", width=15,
                                   font=NORMAL_FONT, command=self.abort_schedule, bg="red")

        # Pack the buttons
        check_button.pack(side=tk.LEFT, padx=10, pady=10)
        self.execute_button.pack(side=tk.LEFT, padx=10, pady=10)
        abort_button.pack(side=tk.LEFT, padx=10, pady=10)

    def setup_frequency_frame(self):
        # Tuning label
        tuning_label = tk.Label(self.frequency_frame, text="Frequency Tunings", font=TITLE_FONT)
        tuning_label.pack(pady=5)

        # Frame for Tuning A, B, C, D (side by side)
        tuning_frame = tk.Frame(self.frequency_frame)
        tuning_frame.pack(fill=tk.X, padx=5, pady=5)

        checkbox_frame = tk.Frame(self.frequency_frame)
        checkbox_frame.pack(fill=tk.X, padx=5, pady=5)

        # Tuning A, B, C, D labels and text boxes
        tuning_a_label = tk.Label(tuning_frame, text="Tuning A:",
                                  font=NORMAL_FONT)
        tuning_a_label.pack(side=tk.LEFT, padx=5, pady=5)
        self.tuning_a = tk.Entry(tuning_frame, width=8,
                                 font=NORMAL_FONT)
        self.tuning_a.pack(side=tk.LEFT, padx=5, pady=5)
        self.to_enable_disable.append(self.tuning_a)

        tuning_b_label = tk.Label(tuning_frame, text="Tuning B:",
                                  font=NORMAL_FONT)
        tuning_b_label.pack(side=tk.LEFT, padx=5, pady=5)
        self.tuning_b = tk.Entry(tuning_frame, width=8,
                                 font=NORMAL_FONT)
        self.tuning_b.pack(side=tk.LEFT, padx=5, pady=5)
        self.to_enable_disable.append(self.tuning_b)

        tuning_c_label = tk.Label(checkbox_frame, text="Tuning C:",
                                  font=NORMAL_FONT)
        tuning_c_label.pack(side=tk.LEFT, padx=5, pady=5)
        self.tuning_c = tk.Entry(checkbox_frame, width=8, 
                                 state=tk.DISABLED,
                                 font=NORMAL_FONT)  # Disabled tuning C
        self.tuning_c.pack(side=tk.LEFT, padx=5, pady=5)

        tuning_d_label = tk.Label(checkbox_frame, text="Tuning D:",
                                  font=NORMAL_FONT)
        tuning_d_label.pack(side=tk.LEFT, padx=5, pady=5)
        self.tuning_d = tk.Entry(checkbox_frame, width=8, 
                                 state=tk.DISABLED,
                                 font=NORMAL_FONT)  # Disabled tuning D
        self.tuning_d.pack(side=tk.LEFT, padx=5, pady=5)

        # Checkboxes for RF gain, IF gain, EQ level
        self.rf_gain_var = tk.IntVar(value=1)  # Checked by default
        self.if_gain_var = tk.IntVar(value=1)  # Checked by default
        self.eq_level_var = tk.IntVar(value=0)  # Unchecked by default
        self.focus_freq_var = tk.IntVar(value=1)  # Unchecked by default

        self.focus_freq = tk.Checkbutton(tuning_frame, text="Focus freq", variable=self.focus_freq_var,
                                    font=NORMAL_FONT)
        self.focus_freq.pack(side=tk.LEFT, padx=5)

        self.rf_gain_checkbox = tk.Checkbutton(tuning_frame, text="RF gain", variable=self.rf_gain_var,
                                    font=NORMAL_FONT)
        self.rf_gain_checkbox.pack(side=tk.LEFT, padx=5)

        self.if_gain_checkbox = tk.Checkbutton(tuning_frame, text="IF gain", variable=self.if_gain_var,
                                    font=NORMAL_FONT)
        self.if_gain_checkbox.pack(side=tk.LEFT, padx=5)

        self.eq_level_checkbox = tk.Checkbutton(tuning_frame, text="EQ level", variable=self.eq_level_var,
                                    font=NORMAL_FONT)
        self.eq_level_checkbox.pack(side=tk.LEFT, padx=5)


        # Add Frequency Setup Button
        add_frequency_button = tk.Button(checkbox_frame, text="Add Frequency Setup", command=self.add_frequency_setup,
                                    font=NORMAL_FONT,
                                    bg="lightblue")
        add_frequency_button.pack(side=tk.LEFT, padx=10)
        self.to_enable_disable.append(add_frequency_button)
        self.to_enable_disable.append(self.rf_gain_checkbox)
        self.to_enable_disable.append(self.if_gain_checkbox)
        self.to_enable_disable.append(self.eq_level_checkbox)
        self.to_enable_disable.append(self.focus_freq)

    def refresh_ant_targets(self):
        # get antenna list
        ant_list = sorted(snap_config.get_rfsoc_active_antlist())
        self.antenna_dropdown.update_options(ant_list)

        # get hashpipe recorders
        d = hpguppi_defaults.hashpipe_targets_LoA.copy()
        d.update(hpguppi_defaults.hashpipe_targets_LoB)

        d_list = hashpipe_targets_to_list(d)
        self.targets_dropdown.update_options(d_list)


    def register_oic(self):
        if self.observer.get():
            self.observer.config(state=tk.DISABLED)
            self.register_oic_button.config(state=tk.DISABLED)
            self.registered_observer = self.observer.get()
            oic = self.registered_observer

            self.write_status(f'Observer {oic} registered as Observer in Charge')

            # Get slack token and channel_id
            slack_token = os.environ.get("ATATOKEN", "")
            channel_id = os.environ.get("ATACHANNEL", "")
            emoji = ":large_green_circle:"
            message_text = f'{emoji} Observer *`{oic}`* registered as Observer In Charge {emoji}'

            if self.enable_slack:
                try:
                    send_slack_message(slack_token, channel_id, message_text)
                    self.write_status(f'Observer {oic} registered as OIC',
                            fg='green')
                except Exception as e:
                    self.write_status(f'Could not send OIC message to slack',
                            fg='red')
                    print(e)


    def deregister_oic(self):
        self.observer.config(state=tk.NORMAL)
        self.register_oic_button.config(state=tk.NORMAL)

        oic = self.registered_observer

        if oic and self.enable_slack:
            # Get slack token and channel_id
            slack_token = os.environ.get("ATATOKEN", "")
            channel_id = os.environ.get("ATACHANNEL", "")
            emoji = ":large_red_square:"
            message_text = f'{emoji} Observer *`{oic}`* de-registered as Observer In Charge {emoji}'

            try:
                self.write_status(f'Observer {oic} de-registered as OIC',
                        fg='green')
                send_slack_message(slack_token, channel_id, message_text)
            except Exception as e:
                self.write_status(f'Could not send deregister OIC message to slack',
                        fg='green')
                print(e)

        self.registered_observer = ""

    def add_backend_setup(self):
        # Get selected values from dropdown menus
        project_id = self.projectid_dropdown.get()
        backend = self.backend_dropdown.get()
        postprocessor = self.postprocessor_dropdown.get()

        # Check if all selections have values
        if project_id and backend and postprocessor:
            # Format the entry to be added to the listbox
            cmd_type = "BACKEND"
            entry = cmd_type + (12 - len(cmd_type))*" "
            entry += f"-- ProjectID: {project_id}, Backend: {backend}, Postprocessor: {postprocessor}"
            self.listbox.insert(tk.END, entry)
            self.disable_execute()
        else:
            self.write_status("Please select all fields.", fg='orange')

    def add_digitizer_mode(self):
        digitizer_mode = self.digitizer_mode_dropdown.get()

        if digitizer_mode:
            cmd_type = "DIGITIZER"
            entry = cmd_type + (12 - len(cmd_type))*" "
            entry += f"-- Mode: {digitizer_mode}"
            self.listbox.insert(tk.END, entry)
            self.disable_execute()
        else:
            self.write_status("Please select digitizer mode.", fg='orange')

    def add_int_length_setup(self):
        # Get selected values from dropdown menus
        int_length = int(self.int_length_entry.get())

        # Check if integration length exists
        if project_id and backend and postprocessor:
            # Format the entry to be added to the listbox
            entry = f"-- INT_LENGTH -- Project: {project_id}, Backend: {backend}, Postprocessor: {postprocessor}"
            self.listbox.insert(tk.END, entry)
        else:
            self.write_status("Please select all fields.", fg='orange')

    def add_frequency_setup(self):
        # Get tuning values and checkboxes
        tuning_a = self.tuning_a.get()
        tuning_b = self.tuning_b.get()
        rf_gain = self.rf_gain_var.get()
        if_gain = self.if_gain_var.get()
        eq_level = self.eq_level_var.get()
        focus_freq = self.focus_freq_var.get() 

        # Format the entry for the frequency setup
        cmd_type = "SETFREQ"
        entry = cmd_type + (12 - len(cmd_type)) * " "
        entry += f"-- TuningA: {tuning_a}, TuningB: {tuning_b}, RFgain: {rf_gain}, IFgain: {if_gain}, EQlevel: {eq_level}, Focus: {focus_freq}"
        self.listbox.insert(tk.END, entry)
        self.disable_execute()

    def add_source_entry(self):
        # Get values from the source name and observation time entries
        source_name = self.source_name_entry.get()
        obs_time = self.obs_time_entry.get()

        if not is_positive_number(obs_time):
            self.write_status(f"Please input a number in Obs Time", fg='red')
            return

        # Check if both fields are filled
        if source_name and obs_time:
            # Format the entry and insert it into the listbox
            cmd_type = "TRACK"
            entry = cmd_type + (12 - len(cmd_type)) * " "
            entry += f"-- Source: {source_name}, ObsTime: {obs_time}"
            self.listbox.insert(tk.END, entry)
            self.disable_execute()

            # Clear the input fields after adding
            self.source_name_entry.delete(0, tk.END)
            self.obs_time_entry.delete(0, tk.END)
        else:
            self.write_status("Please enter both source name and obs time.", fg='orange')

    def reset_time(self):
        dt_now = datetime.datetime.now(
                tz=pytz.timezone(self.tz_dropdown.get()))
        hh_now, mm_now, ss_now = dt_now.hour, dt_now.minute, dt_now.second

        self.date_entry.set_date(dt_now)
        self.hours_spin.delete(0, tk.END)
        self.minutes_spin.delete(0, tk.END)
        self.seconds_spin.delete(0, tk.END)
        self.hours_spin.insert(0, f'{hh_now:02}')
        self.minutes_spin.insert(0, f'{mm_now:02}')
        self.seconds_spin.insert(0, f'{ss_now:02}')

    def add_park_command(self):
        cmd_type = "SETAZEL"
        entry = cmd_type + (12 - len(cmd_type)) * " "
        entry += f"-- Az: 0, El: 18 "
        self.listbox.insert(tk.END, entry)
        self.disable_execute()

    def duplicate_entry(self):
        # Get the selected entries
        selected = self.listbox.curselection()
        if selected:
            for index in selected: #selected[::-1]:  # Reverse order to keep the positions consistent while duplicating
                entry = self.listbox.get(index)
                self.listbox.insert(tk.END, entry)
                self.disable_execute()
        else:
            self.write_status("Please select entried to duplicate", fg='orange')

    def delete_entry(self, event=None):
        # Get the selected entries
        selected = self.listbox.curselection()
        if selected:
            for index in selected[::-1]:  # Reverse order to avoid index shifting issues while deleting
                self.listbox.delete(index)
                self.disable_execute()

    def reset_selection(self, event=None):
        """Reset the selection of the listbox."""
        self.listbox.selection_clear(0, tk.END)  # Clear selection

    def on_click(self, event):
        """Handle mouse click on the listbox."""
        self.dragging = True
        self.dragged_index = self.listbox.nearest(event.y)  # Get the index of the clicked item

    def on_drag(self, event):
        """Handle dragging of listbox items."""
        if self.dragging:
            current_index = self.listbox.nearest(event.y)  # Get the index of the item currently under the mouse
            if current_index != self.dragged_index:
                # Move the item if the indices are different
                item_text = self.listbox.get(self.dragged_index)
                self.listbox.delete(self.dragged_index)
                self.listbox.insert(current_index, item_text)
                self.dragged_index = current_index  # Update the index of the dragged item

    def on_release(self, event):
        """Handle mouse release to stop dragging."""
        self.dragging = False
        self.dragged_index = None  # Reset the dragged index

    def move_entry_up(self):
        selected = self.listbox.curselection()
        if not selected:
            return

        for index in selected:
            if index == 0:  # If it's already at the top, do nothing
                continue
            # Get the text of the selected entry and swap it with the one above
            text = self.listbox.get(index)
            self.listbox.delete(index)
            self.listbox.insert(index - 1, text)
            self.listbox.selection_set(index - 1)  # Keep the moved item selected

    def move_entry_down(self):
        selected = self.listbox.curselection()
        if not selected:
            return

        max_index = self.listbox.size() - 1
        for index in reversed(selected):  # Reverse to avoid index shifting issues
            if index == max_index:  # If it's already at the bottom, do nothing
                continue
            # Get the text of the selected entry and swap it with the one below
            text = self.listbox.get(index)
            self.listbox.delete(index)
            self.listbox.insert(index + 1, text)
            self.listbox.selection_set(index + 1)  # Keep the moved item selected

    def move_selection_up(self):
        selected = self.listbox.curselection()
        if not selected:
            return

        new_selection = []
        for index in selected:
            if index == 0:  # If it's already at the top, do nothing
                continue
            new_selection.append(index - 1)

        self.listbox.selection_clear(0, tk.END)  # Clear the current selection
        for index in new_selection:
            self.listbox.selection_set(index)  # Set the new selection

    def move_selection_down(self):
        selected = self.listbox.curselection()
        if not selected:
            return

        max_index = self.listbox.size() - 1
        new_selection = []
        for index in reversed(selected):  # Reverse to prevent selection shifting issues
            if index == max_index:  # If it's already at the bottom, do nothing
                continue
            new_selection.append(index + 1)

        self.listbox.selection_clear(0, tk.END)  # Clear the current selection
        for index in new_selection:
            self.listbox.selection_set(index)  # Set the new selection

    def open_log_window(self):
        # Check if the log window is already open
        if self.log_window is None or not self.log_window.winfo_exists():
            self.log_window = LogWindow(self)
        else:
            self.log_window.focus()

    def log_message(self, message, color):
        # Log a message to the log window if it's open
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.add_log(message, color)
        else:
            print("Log window is not open.")

    def open_check_source(self):
        app = SourceWidget(self)


    def show_help(self):
        """Display a larger Help dialog with scrollable content."""
        help_window = tk.Toplevel(self)
        help_window.title("Help - How to Use")
        help_window.geometry("300x150")  # Set size for larger window

        help_text_widget = tk.Text(help_window, wrap="word")
        help_text_widget.pack(expand=True, fill=tk.BOTH)

        help_text = ("help text")

        help_text_widget.insert(tk.END, help_text)
        help_text_widget.config(state=tk.DISABLED)

        ok_button = tk.Button(help_window, text="OK", command=help_window.destroy,
                font=("helvetica", 12))
        ok_button.pack()

    def new_schedule(self):
        if self.listbox.get(0, tk.END) != self.original_listbox:
            response = messagebox.askyesnocancel(title="", 
                        message="Current schedule is mofified, want to save it?")
            if response == None:
                # user cancelled
                return

            if response:
                # user decided to save schedule
                self.save_schedule()

            if not response:
                # user didn't want to save, disregarding
                pass

        self.write_status("New schedule")
        self.deregister_oic()
        self.observer.delete(0, tk.END)

        self.refresh_ant_targets()

        # load project IDs
        self.load_project_id_json()
        self.load_backends_json()
        self.load_postprocessors_json()

        self.projectid_dropdown.set("")
        self.projectid_dropdown['values'] = list(self.projectid_mapping.keys())

        self.backend_dropdown.set("")
        self.backend_dropdown['values'] = []
        self.postprocessor_dropdown.set("")
        self.postprocessor_dropdown['values'] = []

        self.tuning_a.delete(0, tk.END)
        self.tuning_b.delete(0, tk.END)
        #self.tuning_c.delete(0, tk.END)
        #self.tuning_d.delete(0, tk.END)

        self.rf_gain_var.set(1)
        self.if_gain_var.set(1)
        self.eq_level_var.set(0)
        self.focus_freq_var.set(1)

        self.source_name_entry.delete(0, tk.END)
        self.obs_time_entry.delete(0, tk.END)

        self.listbox.delete(0, tk.END)
        self.original_listbox = ()
        self.title(f"Allen Telescope Array Scheduler")
        self.write_status(text="")

    def check_if_modified_and_quit(self):
        if self.listbox.get(0, tk.END) != self.original_listbox:
            response = messagebox.askyesnocancel(title="",
                        message="Current schedule is mofified, want to save it?")
            if response == None:
                # User cancelled
                return

            if response:
                # user decided to save schedule
                self.save_schedule()

            if not response:
                # user didn't want to save, disregarding
                pass

        self.quit()
        self.destroy()

    def open_schedule(self):
        if self.listbox.get(0, tk.END) != self.original_listbox:
            response = messagebox.askyesnocancel(title="", 
                        message="Current schedule is mofified, want to save it?")
            if response == None:
                # user cancelled
                return

            if response:
                # user decided to save schedule
                self.save_schedule()

            if not response:
                # user didn't want to save, disregarding
                pass

        # Open the JSON file
        try:
            filename = tk.filedialog.askopenfilename(
                    title="Open Observing Schedule JSON File",
                    filetypes=(("Schedule files", "*.sch"), ("All files", "*.*")))
      
            if not filename:
                return # User cancelled the file selection

            with open(filename, 'r') as json_file:
                data = json.load(json_file)

            self.listbox.delete(0, tk.END)
            #for entry in data['commands']:
            #    self.listbox.insert(tk.END, entry)
            for cmd in data['commands']:
                cmd_type = list(cmd.keys())[0]
                cmd_dict = cmd[cmd_type]
                #cmd_type, cmd_dict
                entry = str(cmd_type) + (12 - len(cmd_type))*" " + "--"
                for elem,val in cmd_dict.items():
                    entry += f" {elem}: {val},"
                entry = entry[:-1] #remove last ,
                self.listbox.insert(tk.END, entry)

        except Exception as e:
            raise e

        self.refresh_ant_targets()

        self.original_listbox = self.listbox.get(0, tk.END)
        fname = os.path.basename(filename)
        self.title(f"Allen Telescope Array Scheduler - {fname}")
        self.write_status(text="")


    def save_schedule(self):
        try:
            filename = tk.filedialog.asksaveasfilename(
                    title="Save Observing Schedule JSON File",
                    filetypes=(("Schedule files", "*.sch"), ("All files", "*.*")))

            if not filename:
                return # User cancelled the file selection

            self.write_status(f"Trying to save to file: {filename}")
            data = self.sch_listbox_to_json()

            with open(filename, "w") as json_file:
                json.dump(data, json_file, indent=4)
        except Exception as e:
            raise e
        self.original_listbox = self.listbox.get(0, tk.END)
        fname = os.path.basename(filename)
        self.title(f"Allen Telescope Array Scheduler - {fname}")


    def sch_listbox_to_list(self):
        cmd_config = []
        for idx, cmd in enumerate(self.listbox.get(0, tk.END)):
            cmd_type, config = self.parse_command(cmd)
            cmd_config.append([cmd_type, config])

        return cmd_config


    def sch_listbox_to_json(self):
        commands_str = self.listbox.get(0, tk.END)
        data = {
                "commands": []
                }

        cmd_list = []
        for cmd in commands_str:
            cmd_type, config = self.parse_command(cmd, supplement=False)
            cmd_list.append({cmd_type: config})

        data["commands"] = cmd_list

        return data


    def check_schedule(self):
        self.write_status("Checking schedule")
        self.disable_everything()
        cmds_cfgs = self.sch_listbox_to_list()
        try:
            obs = self.generate_obs_plan(cmds_cfgs)
        except:
            self.write_status("Check schedule failed", fg='red')
            self.enable_everything()
            raise

        for cmd_type, config in cmds_cfgs:
            if cmd_type == "WAITPROMPT":
                t = f"WARNING: can't predict accurate observing schedule past WAITPROMPT, I will assume {WAIT_FOR_PROMPT_DEFAULT}"
                self.write_status(t, fg='dark orange')

        # Spawn a secondary plot for the obsplanner and wait until exit
        obs_plot = ObsPlotAppSecondary(self, obs)
        self.wait_window(obs_plot)

        if obs_plot.app.plan_has_error():
            self.write_status("Schedule has an error, please fix and check again", fg='red')
        elif obs_plot.app.plan_has_warning():
            self.write_status("Source might set during observing, please proceed with caution", fg='orange')
            self.enable_execute()
        elif obs_plot.app.plan_is_ok():
            self.write_status("No error in plan, it is safe to execute schedule")
            self.enable_execute()

        self.enable_everything()

    def enable_execute(self):
        #self.execute_button.config(state=tk.NORMAL)
        self.execute_button_enabled = True


    def disable_execute(self):
        if self.ignore_check_schedule:
            return
        else:
            #self.execute_button.config(state=tk.DISABLED)
            self.execute_button_enabled = False


    def is_execute_enabled(self):
        return self.execute_button_enabled


    def gui_process_queue(self):
        """
        Method to process GUI application wide events that are feed back from
        other threads.
        This method should run in an infinite (.after(100)) loop
        """
        while not self.task_queue.empty():
            params = self.task_queue.get()
            event_name = params['event_name']
            event_args = params['event_args']

            if event_name == "log_message":
                self.log_message(**event_args)

            elif event_name == "obs_status":
                self.obs_status.config(**event_args)

            elif event_name == "change_color_of_entry":
                self._change_color_of_selected_entry(**event_args)

            elif event_name == "enable_everything":
                self._enable_everything()

            elif event_name == "disable_everything":
                self._disable_everything()

        # Schedule the next queue check
        self.after(100, self.gui_process_queue)


    def write_status(self, text, fg='green'):
        #self.obs_status.config(text=text, fg=fg, font=NORMAL_FONT)
        event = {"event_name": "obs_status",
                "event_args": {"text": text, "fg": fg, "font": NORMAL_FONT}}
        self.task_queue.put(event)

        if text:
            d = datetime.datetime.now()
            log_text = "[" + d.strftime(LOGGING_DTFMT)[:-3] + "]"
            log_text += f": {text}"
            #self.log_message(message=log_text, color=fg)
            event = {"event_name": "log_message",
                    "event_args": {"message": log_text, "color": fg}}
            self.task_queue.put(event)

        # logger() is thread-safe, so this can stay here (and not put in queue)
        if fg.lower() in LOGGING_INFO_COLOR:
            self.logger.info(text)

        if fg.lower() in LOGGING_WARNING_COLOR:
            self.logger.warning(text)

        if fg.lower() in LOGGING_ERROR_COLOR:
            self.logger.error(text)


    def execute_schedule(self):
        """
        I am trying to seperate any GUI-related operations in the
        _execute_schedule() function because that's run in a
        multiprocessing.Process(). So I get everything I need here
        and pass them as context to the function
        """
        #threading.Thread(target=self._execute_schedule, daemon=True).start()

        # Create a pipe to trigger abort
        send_conn, recv_conn = multiprocessing.Pipe()
        self.pipe_conn = send_conn

        context = {"registered_observer": self.registered_observer,
                "is_execute_enabled": self.is_execute_enabled(),
                "write_status": self.write_status,
                "ant_list": self.antenna_dropdown.get_selected_options(),
                "cmds_cfgs": self.sch_listbox_to_list(),
                "recv_conn": recv_conn}

        #print(self.is_execute_enabled())

        multiprocessing.Process(target=self._execute_schedule,
                args=(context,) , daemon=False).start()


    def _execute_schedule(self, context):
        registered_observer = context['registered_observer']
        is_execute_enabled  = context['is_execute_enabled']
        ant_list            = context['ant_list']
        cmds_cfgs           = context['cmds_cfgs']
        recv_conn           = context['recv_conn']


        if registered_observer == "":
            self.write_status("Please register as OIC first", fg='red')
            return

        self.write_status("Executing new schedule")

        if not is_execute_enabled:
            self.write_status("Please run 'Check Schedule' first", fg='red')
            return

        #self.interrupt_flag = False
        self.disable_everything()

        # Reserve antennas first
        try:
            #ant_list   = self.antenna_dropdown.get_selected_options()
            config = {'ant_list': ant_list}
            cmd_type = "RESERVEANTENNAS"
            reserve_antennas = ScheduleExecutor(cmd_type, config, self.write_status)
            reserve_antennas.execute()
        except Exception as e:
            self.enable_everything()
            self.write_status(e.args, fg='red')
            self.write_status("Maybe antennas already reserved? Try running 'atareleaseants' command",
                    fg='red')
            raise e


        # make sure I can release antennas
        cmd_type = "RELEASEANTENNAS"
        release_antennas = ScheduleExecutor(cmd_type, config, self.write_status)

        #cmds_cfgs = self.sch_listbox_to_list()

        # I will initialize all sch lines to make sure
        # all of them are compliant
        schs = []
        for cmd_cfg in cmds_cfgs:
            cmd_type, config = cmd_cfg
            try:
                sch = ScheduleExecutor(cmd_type, config, self.write_status)
            except Exception as e:
                err_txt = f"Initializing schedule line {cmd_type} with "\
                        f"config: {config} failed with exception:"
                self.write_status(err_txt, fg='red')
                self.write_status(e.args[0], fg='red')
                self.enable_everything()
                release_antennas.execute()
                raise e
            schs.append(sch)

        # Let's start executing the schedule
        for idx in range(len(cmds_cfgs)):
            # I'll keep regenerate the ODS file 
            self.generate_ods(cmds_cfgs[idx:])

            #if self.interrupt_flag:
            if recv_conn.poll():
                # User requested interrupt
                # Should be fine to return here because nothing is 
                # being executed
                self.enable_everything()
                release_antennas.execute()
                return

            # current schedule line
            sch      = schs[idx]
            cmd_type = sch.action_type 
            config   = sch.config

            self.write_status(text=cmd_type)
            self.write_status(text=config)
            self.change_color_of_selected_entry(idx)

            # now let's execute the schedule line in a thread
            task_thread = ExceptionThread(target=sch.execute) #ExceptionThread(target=sch.execute)
            task_thread.start()
            while task_thread.is_alive():
            #    if self.interrupt_flag:
                if recv_conn.poll(): # received a stop
                    # try to gracefully interrupt the process 
                    # by passing the interrupt flag
                    sch.interrupt()
                time.sleep(0.2)

            if task_thread.exception:
                self.write_status(task_thread.exception.args[0], fg='red')
                self.enable_everything()
                release_antennas.execute()
                task_thread.join()
                raise task_thread.exception

            # make sure to join 
            task_thread.join()

        self.change_color_of_selected_entry(idx+1)
        idx = 0
        self.enable_everything()
        release_antennas.execute()
        self.write_status("Finished Schedule!")

    def generate_obs_plan(self, cmds_cfgs):
        dt_now = datetime.datetime.now(timezone.utc)

        obs = ObsPlan(Time(dt_now), slew_time=True, obs_overhead=True)

        init_position_set = False

        for cmd_type, config in cmds_cfgs:
            if not init_position_set:
                if 'ant_list' in config:
                    ant_list = config['ant_list']
                    obs.set_current_position(ant_list)
                    #print("setting antenna positions")
                    #print(obs.current_position)
                    init_position_set = True

            if cmd_type == "SETFREQ":
                obs.add_rf_if_overhead()
            elif cmd_type == "BACKEND":
                obs.add_backend_overhead()
            elif cmd_type == "TRACK":
                try:
                    obs.add_obs_block(config['Source'], int(config['ObsTime'])) #this need try/except
                except Exception as e:
                    source = config['Source']
                    self.write_status(f"adding source {source} failed...", fg='red')
                    self.write_status(e.args[0], fg='red')
                    raise e

            elif cmd_type == "WAITPROMPT":
                # I will assume the user will wait for 
                # WAIT_FOR_PROMPT_DEFAULT
                obs.add_wait_time(WAIT_FOR_PROMPT_DEFAULT)
            elif cmd_type == "WAITFOR":
                obs.add_wait_time(int(config['twait']))
            elif cmd_type == "WAITUNTIL":
                dt_until = datetime.datetime.strptime(config['dt'],
                        WAIT_DTFMT)
                obs.add_wait_until_dt(Time(dt_until))

        return obs

    def generate_ods(self, cmds_cfgs):
        obs = self.generate_obs_plan(cmds_cfgs)

        # not to be confused with obs :)
        ods = ods_engine.ODS(output='ERROR')
        ods.get_defaults_dict(ODS_DEFAULTS)
        ods_list = []

        for obs_entry in obs.obs_plan:
            entry = {}
            entry['src_id'] = obs_entry['object']
            entry['src_ra_j2000_deg'] = obs_entry['ra'] * 360 / 24.
            entry['src_dec_j2000_deg'] = obs_entry['dec']
            entry['src_start_utc'] = obs_entry['start_time'].isot
            entry['src_end_utc'] = obs_entry['end_time'].isot

            ods_list.append(entry)

        if ods_list:
            ods.add_from_list(ods_list)
            ods.write_ods(ODS_WRITE)
            #tstamp = str(round(time.time(), 2))
            #ods.write_ods(f"/home/sonata/ods_files/ods_{tstamp}.json")


    
    def abort_schedule(self):
        self.write_status("Interrupt requested!", fg='red')
        if self.pipe_conn:
            try:
                self.pipe_conn.send("stop")
            # means that the end of the pipe is broken, which is fine
            # if no observation is being conducted and the user requested
            # an interrupt
            except BrokenPipeError as e:
                pass
        #self.interrupt_flag = True

    def parse_command(self, command, supplement=True):
        res = parse("{cmd_type} -- {cfg_str}", command)
        cmd_type, cfg_str = res['cmd_type'], res['cfg_str']

        cmd_type = cmd_type.strip().replace(" ", "_")
        cfg = self.str_to_dict(res['cfg_str'])
        
        # supplement the configuration
        ant_list   = self.antenna_dropdown.get_selected_options()
        hp_targets = list_to_hashpipe_targets(self.targets_dropdown.get_selected_options())

        if supplement:
            if cmd_type == "SETFREQ":
                cfg['ant_list'] = ant_list
            if cmd_type == "BACKEND":
                cfg['hp_targets'] = hp_targets
            if cmd_type == "TRACK":
                cfg['ant_list'] = ant_list
                cfg['hp_targets'] = hp_targets
            if cmd_type == "SETAZEL":
                cfg['ant_list'] = ant_list

        return cmd_type, cfg


    def str_to_dict(self, cfg_str):
        splt = cfg_str.split(",")
        
        cfg = {}
        for keyval in splt:
            key, val = keyval.split(":")
            key = key.strip().replace(" ", "_")

            cfg[key] = val.strip()

        return cfg

    def change_color_of_selected_entry(self, selected_index):
        """
        Run this in the "queue" system
        """
        event = {"event_name": "change_color_of_entry",
                "event_args": {"selected_index": selected_index}}
        self.task_queue.put(event)


    def _change_color_of_selected_entry(self, selected_index):
        # Get all current entries
        current_items = self.listbox.get(0, tk.END)
        
        # Clear the listbox
        self.listbox.delete(0, tk.END)
        
        # Reinsert all entries, and change the background color for the selected entry
        for i, item in enumerate(current_items):
            self.listbox.insert(tk.END, item)
            # To keep in mind:
            # Linux (some platforms): In some environments, 
            # itemconfig() may not function as expected due 
            # to theme-related constraints.
            if i < selected_index:
                self.listbox.itemconfig(i, {'bg': 'grey'})
            elif i == selected_index:
                self.listbox.itemconfig(i, {'bg': 'lightgreen'})  # Change color of selected entry
            else:
                self.listbox.itemconfig(i, {'bg': self.listbox.cget("bg")})  # Default color for other entries
        #self.update()

    def disable_everything(self):
        """
        Run this in the "queue" system
        """
        event = {"event_name": "disable_everything",
                "event_args": None}
        self.task_queue.put(event)

    def _disable_everything(self):
        for button in self.to_enable_disable + self.to_readonly_disable:
            button.config(state=tk.DISABLED)

        #self.tuning_a.config(state=tk.DISABLED)
        #self.tuning_b.config(state=tk.DISABLED)


    def enable_everything(self):
        """
        Run this in the "queue" system
        """
        event = {"event_name": "enable_everything",
                "event_args": None}
        self.task_queue.put(event)


    def _enable_everything(self):
        for button in self.to_enable_disable:
            button.config(state=tk.NORMAL)
        for button in self.to_readonly_disable:
            button.config(state="readonly")

        #self.tuning_a.config(state=tk.NORMAL)
        #self.tuning_b.config(state=tk.NORMAL)

    def load_project_id_json(self, projectid_fname=PROJECTID_FNAME):
        with open(projectid_fname, 'r') as json_file:
            self.projectid_mapping = json.load(json_file)

    def load_backends_json(self, backends_fname=BACKENDS_FNAME):
        with open(backends_fname, 'r') as json_file:
            self.backends_mapping = json.load(json_file)

    def load_postprocessors_json(self, postprocessors_fname=POSTPROCESSORS_FNAME):
        with open(postprocessors_fname, 'r') as json_file:
            self.postprocessors_mapping = json.load(json_file)

    def update_backend_combobox(self, event=None):
        self.postprocessor_dropdown.set('')
        self.postprocessor_dropdown['values'] = []
        self.backend_dropdown.set('')
        self.backend_dropdown['values'] = []


        project_id = self.projectid_dropdown.get()
        backends = list(self.projectid_mapping[project_id]['Backend'].keys())

        self.load_backends_json()

        for backend in backends:
            if backend not in self.backends_mapping.keys():
                self.write_status(f"'{backend}' not in backends.json file, please define it...",
                        fg='red')
                raise RuntimeError("Backend doesn't exist in config")

        self.backend_dropdown['values'] = backends
        #self.update()

    def update_postprocessor_combobox(self, event=None):
        self.postprocessor_dropdown.set('')
        self.postprocessor_dropdown['values'] = []
        #self.update()

        project_id = self.projectid_dropdown.get()
        backend = self.backend_dropdown.get()
        postprocessors = list(self.projectid_mapping[project_id]['Backend'][backend]['Postprocessor'])

        self.load_postprocessors_json()

        for postprocessor in postprocessors:
            if postprocessor not in self.postprocessors_mapping.keys():
                self.write_status(f"'{postprocessor}' not in postprocessors.json file, please define it...",
                        fg='red')
                raise RuntimeError("Postprocessor doesn't exist in config")

        self.postprocessor_dropdown['values'] = postprocessors
        #self.update()


    def wait_until(self, event=None):
        date_selected = self.date_entry.get()
        hours = int(self.hours_spin.get())
        minutes = int(self.minutes_spin.get())
        seconds = int(self.seconds_spin.get())

        start_time = datetime.datetime.strptime(
                f"{date_selected} {hours:02}:{minutes:02}:{seconds:02}", 
                "%m/%d/%y %H:%M:%S")
        selected_tz = pytz.timezone(self.tz_dropdown.get())

        localized_time = selected_tz.localize(start_time)
        localized_time_str = localized_time.strftime(WAIT_DTFMT)

        #entry = f"--  WAITUNTIL  -- {localized_time}"
        cmd_type = "WAITUNTIL"
        entry = cmd_type + (12 - len(cmd_type))*" " + f"-- dt: {localized_time_str}"
        self.listbox.insert(tk.END, entry)
        self.disable_execute()

        # to parse back
        # Parse the string including the timezone
        # dt_with_tz = datetime.fromisoformat(datetime_string)



    def wait_for_prompt(self, event=None):
        cmd_type = "WAITPROMPT"
        entry = cmd_type + (12 - len(cmd_type))*" " + f"-- Method: prompt"
        self.listbox.insert(tk.END, entry)
        self.disable_execute()


    def wait_for_seconds(self, event=None):
        wait_time = self.wait_for_entry.get()
        if is_positive_number(wait_time):
            cmd_type = "WAITFOR"
            entry = cmd_type + (12 - len(cmd_type))*" " 
            entry += f"-- twait: {wait_time}"
            self.listbox.insert(tk.END, entry)
            self.disable_execute()


    def start_progress_bar_indefinite(self):
        self.progress_bar.config(mode='indeterminate')
        self.progress_var.set(0)
        self.progress_bar.start()  # Start moving the progress bar

    def end_progress_bar_indefinite(self):
        self.progress_bar.stop()

    def start_progress_bar_definite(self, tprogress):
        self.progress_var.set(0)
        self.tprogress = tprogress
        self.after(1000, self.update_progress_bar, 1)

    def update_progress_bar(self, count):
        # Update the progress bar by 1/60th of the total each second
        print(count)
        self.progress_var.set(count * 100 / self.tprogress)

        if count < self.tprogress:
            self.after(1000, self.update_progress_bar, count + 1)  # Continue updating every second
        else:
            print("Progress completed!")
            return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
            description='ATA observation scheduler')
    parser.add_argument('-d', '--debug', help='Enable debug mode',
            action='store_true')
    parser.add_argument('-i', '--ignore-check', 
            help='Make schedule file always executable; ignore check_schedule',
            action='store_true')

    args = parser.parse_args()

    app = TelescopeSchedulerApp(args)
    app.mainloop()

