import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox
from tkcalendar import DateEntry
from PIL import Image, ImageTk
import json
import time
import threading

from schedule_executor import ScheduleExecutor

import datetime
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

ENABLE_SLACK = False

TITLE_FONT = ("Helvetica", 18)
NORMAL_FONT = ("Helvetica", 14)
FILL_FONT = ("Helvetica", 12)


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


class TelescopeSchedulerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Allen Telescope Array Scheduler")

        # Set window size to 1200x900
        self.root.geometry("1600x850")
        self.interrupt_flag = False
        self.to_enable_disable = [] #list of everything to enable and disable
        self.to_readonly_disable = [] # same as above, but return to readonly

        self.original_listbox = ()

        # load project IDs
        self.load_project_id_json()
        self.load_backends_json()
        self.load_postprocessors_json()

        self.enable_slack = ENABLE_SLACK

        # Configure the root grid layout to have two columns
        #self.root.grid_columnconfigure(0, weight=1)  # Left frame
        #self.root.grid_columnconfigure(1, weight=1)  # Right frame

        # Create the left and right frames
        self.frame_left = tk.Frame(self.root)
        self.frame_right = tk.Frame(self.root)

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
        duplicate_button = tk.Button(listbox_button_frame, text="Duplicate Entries", command=self.duplicate_entry, font=NORMAL_FONT)
        duplicate_button.pack(side=tk.LEFT, padx=10, pady=12, fill=tk.BOTH)
        self.to_enable_disable.append(duplicate_button)

        # Add the "Delete Entry" button
        delete_button = tk.Button(listbox_button_frame, text="Delete Entries", command=self.delete_entry, font=NORMAL_FONT)
        delete_button.pack(side=tk.RIGHT, padx=0)
        self.to_enable_disable.append(delete_button)

        # Error display label
        self.obs_status = tk.Label(self.frame_left, text="", font=('Helvetica', 16))
        self.obs_status.grid(row=4, column=1, padx=10, pady=10)
        #self.obs_status.config(text="Bla, bla")

        # Create a progress bar in indeterminate mode
        #self.progress_var = tk.DoubleVar()
        #self.progress_bar = ttk.Progressbar(self.frame_left, variable=self.progress_var, length=400, maximum=100)
        #self.progress_bar.grid(row=3, column=1, padx=10, pady=10)
        #progress_bar.start()

        # Bind the "Q" key to delete the selected entry
        self.root.bind("<d>", self.delete_entry)

        # Bind the "BackSpace" key to delete the selected entry
        self.root.bind("<BackSpace>", self.delete_entry)

        # Bind the "Escape" key to reset the selection
        self.root.bind("<Escape>", self.reset_selection)

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
        self.menu_bar = tk.Menu(self.root)
        self.root.config(menu=self.menu_bar)

        # Add File menu to the menubar 
        file_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="File", menu=file_menu, font=NORMAL_FONT)
        file_menu.add_command(label="New", command=self.new_schedule, font=NORMAL_FONT)
        file_menu.add_command(label="Open", command=self.open_schedule, font=NORMAL_FONT)
        file_menu.add_command(label="Save", command=self.save_schedule, font=NORMAL_FONT)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.check_if_modified_and_quit, font=NORMAL_FONT)


        # Add Help menu to the menubar
        help_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Help", menu=help_menu, font=NORMAL_FONT)
        help_menu.add_command(label="How to Use", command=self.show_help, font=NORMAL_FONT)
        help_menu.add_separator()
        help_menu.add_command(label="About", font=NORMAL_FONT)

        # check if schedule is modified before exiting
        self.root.protocol("WM_DELETE_WINDOW", self.check_if_modified_and_quit)
        

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
        self.antenna_frame.pack(fill=tk.Y, expand=True, side=tk.LEFT, pady=2)

        antenna_label = tk.Label(self.antenna_frame, text="Select Antennas & Recorders", font=TITLE_FONT)
        antenna_label.pack(fill=tk.X, pady=2)

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
        self.projectid_dropdown = ttk.Combobox(dropdown_frame, values=projectid_options, width=4,
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
        source_frame_right.pack(fill=tk.Y, expand=True, side=tk.LEFT, pady=2)

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
        self.to_enable_disable.append(self.wait_for_entry)
        self.to_enable_disable.append(self.wait_for_button)

        # Button frame - Two buttons
        check_button = tk.Button(self.button_frame, text="Check Schedule", width=15,
                                 font=NORMAL_FONT, command=self.check_schedule, bg="orange")
        self.to_enable_disable.append(check_button)
        execute_button = tk.Button(self.button_frame, text="Execute Schedule", width=15,
                                   font=NORMAL_FONT, command=self.execute_schedule, bg="lightgreen")
        self.to_enable_disable.append(execute_button)
        abort_button = tk.Button(self.button_frame, text="Abort Schedule", width=15,
                                   font=NORMAL_FONT, command=self.abort_schedule, bg="red")

        # Pack the buttons
        check_button.pack(side=tk.LEFT, padx=10, pady=10)
        execute_button.pack(side=tk.LEFT, padx=10, pady=10)
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

            # Get slack token and channel_id
            slack_token = os.environ.get("ATATOKEN", "")
            channel_id = os.environ.get("ATACHANNEL", "")
            oic = self.registered_observer
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
            entry = f"--   BACKEND   -- ProjectID: {project_id}, Backend: {backend}, Postprocessor: {postprocessor}"
            self.listbox.insert(tk.END, entry)
        else:
            print("Please select all fields.")

    def add_digitizer_mode(self):
        digitizer_mode = self.digitizer_mode_dropdown.get()

        if digitizer_mode:
            entry = f"--   DIGITIZER   -- Mode: {digitizer_mode}"
            self.listbox.insert(tk.END, entry)
        else:
            print("Please select digitizer mode")

    def add_int_length_setup(self):
        # Get selected values from dropdown menus
        int_length = int(self.int_length_entry.get())

        # Check if integration length exists
        if project_id and backend and postprocessor:
            # Format the entry to be added to the listbox
            entry = f"-- INT_LENGTH -- Project: {project_id}, Backend: {backend}, Postprocessor: {postprocessor}"
            self.listbox.insert(tk.END, entry)
        else:
            print("Please select all fields.")

    def add_frequency_setup(self):
        # Get tuning values and checkboxes
        tuning_a = self.tuning_a.get()
        tuning_b = self.tuning_b.get()
        rf_gain = self.rf_gain_var.get()
        if_gain = self.if_gain_var.get()
        eq_level = self.eq_level_var.get()

        # Format the entry for the frequency setup
        entry = f"--    SETFREQ    -- TuningA: {tuning_a}, TuningB: {tuning_b}, RFgain: {rf_gain}, IFgain: {if_gain}, EQlevel: {eq_level}"
        self.listbox.insert(tk.END, entry)

    def add_source_entry(self):
        # Get values from the source name and observation time entries
        source_name = self.source_name_entry.get()
        obs_time = self.obs_time_entry.get()

        # Check if both fields are filled
        if source_name and obs_time:
            # Format the entry and insert it into the listbox
            entry = f"--      TRACK     -- Source: {source_name}, ObsTime: {obs_time}"
            self.listbox.insert(tk.END, entry)

            # Clear the input fields after adding
            self.source_name_entry.delete(0, tk.END)
            self.obs_time_entry.delete(0, tk.END)
        else:
            print("Please enter both source name and observation time.")

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
        entry = f"--      PARK    -- Az,el = (0, 180) "
        self.listbox.insert(tk.END, entry)

    def duplicate_entry(self):
        # Get the selected entries
        selected = self.listbox.curselection()
        if selected:
            for index in selected: #selected[::-1]:  # Reverse order to keep the positions consistent while duplicating
                entry = self.listbox.get(index)
                self.listbox.insert(tk.END, entry)
        else:
            print("Please select entries to duplicate.")

    def delete_entry(self, event=None):
        # Get the selected entries
        selected = self.listbox.curselection()
        if selected:
            for index in selected[::-1]:  # Reverse order to avoid index shifting issues while deleting
                self.listbox.delete(index)

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

    def show_help(self):
        """Display a larger Help dialog with scrollable content."""
        help_window = tk.Toplevel(self.root)
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

        self.source_name_entry.delete(0, tk.END)
        self.obs_time_entry.delete(0, tk.END)

        self.listbox.delete(0, tk.END)
        self.original_listbox = ()
        self.root.title(f"Allen Telescope Array Scheduler")
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

        self.root.quit()
        self.root.destroy()

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
            for entry in data['commands']:
                self.listbox.insert(tk.END, entry)

        except Exception as e:
            raise e

        self.refresh_ant_targets()

        self.original_listbox = self.listbox.get(0, tk.END)
        fname = os.path.basename(filename)
        self.root.title(f"Allen Telescope Array Scheduler - {fname}")
        self.write_status(text="")


    def save_schedule(self):
        try:
            filename = tk.filedialog.asksaveasfilename(
                    title="Save Observing Schedule JSON File",
                    filetypes=(("Schedule files", "*.sch"), ("All files", "*.*")))

            if not filename:
                return # User cancelled the file selection

            commands = self.listbox.get(0, tk.END)
            data = {
                    "commands": list(commands)
                    }
            with open(filename, "w") as json_file:
                json.dump(data, json_file, indent=4)
        except Exception as e:
            raise e
        self.original_listbox = self.listbox.get(0, tk.END)
        fname = os.path.basename(filename)
        self.root.title(f"Allen Telescope Array Scheduler - {fname}")

    def check_schedule(self):
        self.disable_everything()
        data = self.generate_planner()
        #filename = "./tmp_obs.json" #XXX replace with tmp file
        json_file = tempfile.NamedTemporaryFile(mode="w", delete=False)
        filename = json_file.name
        json.dump(data, json_file, indent=4)
        json_file.close()

        os.system(f"python ataobsplanner {filename}")
        os.unlink(filename)
        self.enable_everything()

    def generate_planner(self):
        dt = datetime.datetime.now(tz=pytz.timezone(DEFAULT_TZ))
        start_date = dt.strftime("%m/%d/%y")
        start_time = dt.strftime("%H:%M")

        # timezone should always be pacific
        selected_timezone = DEFAULT_TZ

        slew_time = True
        overhead_toggled = False
        sources = []

        for command in self.listbox.get(0, tk.END):
            res = parse('--{cmd_type}--{cmd}', command)
            cmd_type = res['cmd_type'].strip()
            cmd = res['cmd'].strip()

            if cmd_type == "BACKEND":
                overhead_toggled = True
            if cmd_type == "TRACK":
                res = parse('Source: {source_name}, Observation Time: {obs_time}',
                            cmd)
                source_name = res['source_name']
                obs_time = res['obs_time']
                sources.append(f"{source_name}, {obs_time} sec")
        data = {
            "start_date": start_date,
            "start_time": start_time,
            "timezone": selected_timezone,
            "initial_overhead": overhead_toggled,
            "slew_time": True,
            "sources": list(sources)
        }
        return data

    def write_status(self, text, fg='green'):
        self.obs_status.config(text=text, fg=fg, font=NORMAL_FONT)
    
    def execute_schedule(self):
        self.write_status("")

        if self.registered_observer == "":
            self.write_status("Please register as OIC first", fg='red')
            return

        self.interrupt_flag = False
        self.disable_everything()
        idx = 0
        for idx, cmd in enumerate(self.listbox.get(0, tk.END)):
            if self.interrupt_flag:
                self.enable_everything()
                return
            self.write_status(text=cmd)
            self.change_color_of_selected_entry(idx)

            cmd_type, config = self.parse_command(cmd)
            print(cmd_type, config)
            sch = ScheduleExecutor(cmd_type, config, self.write_status)
            task_thread = threading.Thread(target=sch.execute)
            task_thread.start()
            while task_thread.is_alive():
                time.sleep(0.5)
                self.root.update()

            #time.sleep(5)

            self.root.update()
        self.change_color_of_selected_entry(idx+1)
        idx = 0
        self.enable_everything()
        self.write_status("Finished Schedule!")
    
    def abort_schedule(self):
        print("interrupt requested")
        self.interrupt_flag = True

    def parse_command(self, command):
        res = parse("-- {cmd_type} -- {cfg_str}", command)
        cmd_type, cfg_str = res['cmd_type'], res['cfg_str']

        cmd_type = cmd_type.strip().replace(" ", "_")
        cfg = self.str_to_dict(res['cfg_str'])
        
        # supplement the configuration
        ant_list   = self.antenna_dropdown.get_selected_options()
        hp_targets = list_to_hashpipe_targets(self.targets_dropdown.get_selected_options())

        if cmd_type == "SETFREQ":
            cfg['ant_list'] = ant_list
        if cmd_type == "BACKEND":
            cfg['hp_targets'] = hp_targets
        if cmd_type == "TRACK":
            cfg['ant_list'] = ant_list
            cfg['hp_targets'] = hp_targets

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
        self.root.update()

    def disable_everything(self):
        self.root.update()
        for button in self.to_enable_disable + self.to_readonly_disable:
            button.config(state=tk.DISABLED)

        self.root.update()
        #self.tuning_a.config(state=tk.DISABLED)
        #self.tuning_b.config(state=tk.DISABLED)

    def enable_everything(self):
        self.root.update()
        for button in self.to_enable_disable:
            button.config(state=tk.NORMAL)
        for button in self.to_readonly_disable:
            button.config(state="readonly")

        self.root.update()
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

        self.root.update()

        project_id = self.projectid_dropdown.get()
        backends = list(self.projectid_mapping[project_id]['Backend'].keys())

        self.load_backends_json()

        for backend in backends:
            if backend not in self.backends_mapping.keys():
                self.write_status(f"'{backend}' not in backends.json file, please define it...",
                        fg='red')
                raise RuntimeError("Backend doesn't exist in config")

        self.backend_dropdown['values'] = backends
        self.root.update()

    def update_postprocessor_combobox(self, event=None):
        self.postprocessor_dropdown.set('')
        self.postprocessor_dropdown['values'] = []
        self.root.update()

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
        self.root.update()


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

        entry = f"--  WAITUNTIL  -- {localized_time}"
        self.listbox.insert(tk.END, entry)

        # to parse back
        # Parse the string including the timezone
        # dt_with_tz = datetime.fromisoformat(datetime_string)



    def wait_for_prompt(self, event=None):
        entry = f"-- WAITPROMPT -- User input"
        self.listbox.insert(tk.END, entry)
        pass


    def wait_for_seconds(self, event=None):
        wait_time = self.wait_for_entry.get()
        if wait_time:
            entry = f"--   WAITFOR   -- {wait_time} seconds "
            self.listbox.insert(tk.END, entry)


    def start_progress_bar_indefinite(self):
        self.progress_bar.config(mode='indeterminate')
        self.progress_var.set(0)
        self.progress_bar.start()  # Start moving the progress bar

    def end_progress_bar_indefinite(self):
        self.progress_bar.stop()

    def start_progress_bar_definite(self, tprogress):
        self.progress_var.set(0)
        self.tprogress = tprogress
        self.root.after(1000, self.update_progress_bar, 1)

    def update_progress_bar(self, count):
        # Update the progress bar by 1/60th of the total each second
        print(count)
        self.progress_var.set(count * 100 / self.tprogress)

        if count < self.tprogress:
            self.root.after(1000, self.update_progress_bar, count + 1)  # Continue updating every second
        else:
            print("Progress completed!")
            return


if __name__ == "__main__":
    root = tk.Tk()
    app = TelescopeSchedulerApp(root)
    root.mainloop()

