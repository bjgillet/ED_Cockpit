"""
ED Cockpit - Elite Dangerous Log Parser
=======================================
Parses Elite Dangerous journal log files to extract relevant events for 
various agent roles.
It starts from last recorded Journal files and parse to last matching entry for
events of interest up to the last timestamp recorded in active ED Cockît context,
if any.

If no events or fields matches are found, it will search the next older 
log, if any, and will continue parsing until it finds a match or exhausts all log 
files. This way, we can ensure that we are not missing any relevant events 
even if they are not in the most recent log file, as long as they are still 
within one of the log files available in the log directory.

This is important because some events may not be present in the most 
recent log file due to the timing of when the agent starts, when the events 
occured, or if a session was played without agent being active (no context record) 
so we need to check previous log files as well to ensure we capture all relevant 
events to update the agent's and roles contexts.
Also, this is efficient as we only parse logs from the last recorded to the 
most recent, so all informations should be done through 1 to 3 log files scanning.

Agent contexts are timestamped. This means that as soon as we find log record
older than current context timestamp, we can stop parsing further log.
This a watchodg in case you would have years of game logs ;-). 
This makes CPU efficient as we are not parsing logs that are older than 
the current context, the only exception being when you run ED Cockpit for the 
first time and there is no context timestamp, in that case we will parse all 
log files until we find a match or exhaust all log files. As soon we got the last
updated context we exit (job done : context up to date), so, there should 
be only few files to parse as we are starting from the most recent log file and 
going backwards until we find a match or exhaust all log files, so it should be
efficient. 

Never forget ED Cockpit is designed to be efficient and not to consume 
too much CPU resources or RAM, focusing on low profile resources consumption.

Event to watch format : (sent to LogParser at init)
-----------------------
    {
        "<event_name_1>": {
            "fields": [
                "<field_name_1.1>",
                "<field_name_1.2>",
                ...
            ]
        },
        "<event_name_2>": {
            "fields": [
                "<field_name_2.1>",
                "<field_name_2.2>",
                ...
            ]
        ...
    }

Result event dictionary format : (returned by LogParser after parsing)
-------------------------------- 
  As a result of parsing, we will return a list of event dictionaries matching 
  the events.
  Each event dictionary will have the following format:
    {
         "<event_name_1>":{
            {"<field_name_1.1>": ""|"<String>"},
            {"<field_name_1.2>": ""|"<String>"}, 
             ...
            },

         "<event_name_2>": {
            {"<field_name_2.1>": ""|"<String>"},
            {"<field_name_2.2>": ""|"<String>"},
            ...
         },
        ...
    }

Vlaidation of events to stop parsing when items found
-----------------------------------------------------------

 Each time an event is found, we will update event status internally.
 It won't stop the current file parsing but it will stop parsing previous
 log files for that event. This is to be sure we don't stop at first match 
 but got the last update for the event (ie. you swapped ship later on
 in same session but we search for last current ship).

 event_status is built from events_to watch as such :

    {
        "<event_name_1>": <boolean>,  # False means we are still looking for
        "<event_name_2>": <boolean>, 
        ...
    }

"""

import os
import glob
import json
from datetime import datetime
from pathlib import Path

class LogParser :
    """
    Watch at log history for up to date context refresh for client roles.
    Will parse Elite Dangerous log files fomr the most recent to the 
    oldest until it finds matching events or a watchodog counter of files number to parse.
    This is to lower the CPU impact. As soon an event/value is found it will flag it as found
    Continuing research to a previous log file will be done only for events not yet found,
    so we are sure to get last update for each event, in the limit of the logfiles number 
    to parse.
    """

    def __init__(self, log_dir, events_to_watch:dict=None):
        """"
        Initialize the LogParser with the directory containing log files and the events to 
        watch from the agent role caller. Initializes local context for which events/values to search,
        a status to know if it has been found, and building the result structure to fill with 
        found values as far are some remain to find.
        If none are found, then value will be set to an empy string, so up to the 
        caller agent to manage that.
        """
    
        self.log_dir = Path(log_dir)
        self.log_files = list(self.log_dir.glob("Journal*.log"))
        self.log_files=sorted(self.log_files, key=os.path.getmtime, reverse=True)  # Sort by modification time, most recent first
        self.events_to_watch = events_to_watch
        self.event_status = {}
        for ev in self.events_to_watch:
            self.event_status[ev] = False 
        print(f"LogParser initialized with log directory: {log_dir}")
        print(f"Events to watch: {list(self.events_to_watch.keys())}")
        print(f"Initial event status: {self.event_status}")
        self.event_results = {}
        for ev in self.events_to_watch:
            self.event_results[ev] = {}
            for field in self.events_to_watch[ev]["fields"]:
                self.event_results[ev][field] = ""  # Initialize fields with empty strings
        print(f"Initial event results structure: {self.event_results}")

    def parse_logs(self):
        """
        Parses the log files in the given directory and extracts related events.
        Parsing is done from the most recent log file to the oldest until we find all events.
        Returns a list of event dictionaries matching the events_to_watch dictionnary transmitted
        by agent role.
        Only last events found in a file are kept and delivered as result.
        once last matching event/value is found it is flagged as found so previous occurences
        so this event won't be tracked if we gat back in time on previouslog files to find still 
        not found event/values occurence.
        As soon as all events/values are found, parsing is stopped and result is returned.        
        """

       # for item in self.log_files:
       #    self.log_files.remove(item) if not ".log" in item else None

        print(f"log_files = {self.log_files}")
    
        cur_log= open(f"{self.log_files[0]}", 'r')
        #print(f"curlog = {self.log_files[0]}")
        while True:
            line = cur_log.readline()
            if line == '':  # End of file
                break
            json_line = json.loads(line)

            if any(event in line for event in self.events_to_watch):
                # For demonstration, you would replace this with actual parsing logic to extract fields.
                print(f"--S>    Found event in line: {line}")  
                for ev in self.events_to_watch:
                    if json_line.get("event") == ev:
                        print(f"---->    Event {ev} found in line: {json_line}")
                        for field in self.events_to_watch[ev]["fields"]:
                            self.event_results[ev][field] = json_line[field]
                            print(f"------>    Extracted {json_line[field]} for event {ev}: {self.event_results[ev][field]}")
                        self.event_status[ev] = True  # Mark event as found
        print(f"Final event status after parsing: {self.event_status}")
        return self.event_results
                

if __name__ == "__main__":
    events={
        "Loadout": {
            "fields": [
                "Ship",
                "ShipName",
                "FuelCapacity",
                "CargoCapacity"
            ]
        },
        "ProspectedAsteroid": {
            "fields": [
                "Materials",
                "Content",
        #        "MotherlodeType_Localised",
                "Remaining"
            ]
        },
        "AsteroidCracked": {
            "fields": [
                "Body"
            ]
        },
        "MiningRefined": {
            "fields": [
                "Type_Localised"
            ]
        },
        "LaunchDrone": {
            "fields": [
                "Type"
            ]
        }
    }
    log_dir = "C:/Users/SparcT1/Saved Games/Frontier Developments/Elite Dangerous"

    lp = LogParser(log_dir=log_dir, events_to_watch=events)

    result = lp.parse_logs()
    print (f"Extracted events: {result}") 
    #for file in files:        
    #    print(file) if "Journal" in file else None
