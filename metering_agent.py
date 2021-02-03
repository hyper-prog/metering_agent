#!/usr/bin/env python3
"""
 Metering Agent - An agent which collect metering informations
                  from local system and sends to various targets

    Usage: ./metering_agent.py <config.yml>

    Author: Deák Péter (hyper80@gmail.com)

"""
import sys
import time
import math
import os
import yaml
import json
import datetime
import mysql.connector
import psycopg2
from time import sleep
import pkg_resources
from pkg_resources import DistributionNotFound, VersionConflict
from importlib.machinery import SourceFileLoader

dependencies = [
  'PyYAML>=5.1.0',
  'mysql-connector-python>=2.1.0',
  'psycopg2>=2.6.0',
]

databases = {}

class DatabaseConnection:
    def __init__(self,host,name,user,pswd):
        self._host = host
        self._name = name
        self._user = user
        self._pswd = pswd
        self._conn = None

    def connect(self):
        pass

    def disconnect(self):
        pass

    def execute_noresult_sql(self,sql,pars = []):
        cur = self._conn.cursor()
        cur.execute(sql,tuple(pars))
        self._conn.commit()

    def execute_onevalue_sql(self,sql,pars = []):
        cur = self._conn.cursor()
        cur.execute(sql,tuple(pars))
        confrow = cur.fetchone()
        self._conn.commit()
        return confrow[0]

    def current_timestamp_string(self):
        pass

class DatabaseConnection_Mysql(DatabaseConnection):
    def __init__(self,host,name,user,pswd):
        super().__init__(host,name,user,pswd)

    def connect(self):
        try:
            self._conn = mysql.connector.connect(host=self._host,database=self._name,
                                                user=self._user,password=self._pswd)
        except mysql.connector.Error as err:
            if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                print("DatabaseConnectionMysql connection: user name or password error")
            elif err.errno == errorcode.ER_BAD_DB_ERROR:
                print("DatabaseConnectionMysql connection: database does not exist")
            else:
                print("DatabaseConnectionMysql connection error:" + str(err))

    def disconnect(self):
        self._conn.close()
        self._conn = None

    def current_timestamp_string(self):
        return "CURRENT_TIMESTAMP"

class DatabaseConnection_Pgsql(DatabaseConnection):
    def __init__(self,host,name,user,pswd):
        super().__init__(host,name,user,pswd)

    def connect(self):
        try:
            self._conn = psycopg2.connect(host=self._host,database=self._name,
                                         user=self._user,password=self._pswd)
        except psycopg2.OperationalError as e:
            print("DatabaseConnectionPgsql connect error: " + e)

    def disconnect(self):
        self._conn.close()
        self._conn = None

    def current_timestamp_string(self):
        return "now()"

# ##############################################################################

class CollectorBase:
    def __init__(self,name,shortname,datatype,mtype,par = {}):
        self.name = name
        self.shortname = shortname
        self.datatype = datatype
        self.result = None
        self.mtype = mtype
        self._measured = None
        self._last_measured = None

    def read_low(self,sec):
        pass

    def read(self,sec):
        self._measured = self.read_low(sec)
        if self.mtype == "passthroug":
            self.result = self._measured
        elif self.mtype == "div_by_1024":
            self.result = self._measured / 1024
        elif self.mtype == "div_by_1024_1024":
            self.result = self._measured / 1048576
        else:
            if self._last_measured == None or sec == 0:
                self.result = None
            else:
                if self.mtype == "increment":
                    self.result = self._measured - self._last_measured
                if self.mtype == "increment_in_sec":
                    self.result = (self._measured - self._last_measured) / sec
                if self.mtype == "increment_in_minute":
                    self.result = ((self._measured - self._last_measured) * 60) / sec
        self._last_measured = self._measured
        return self.result

    def convert_to_reqtype(self,val):
        if self.datatype == "int":
            return int(val)
        if self.datatype == "float":
            return float(val)
        if self.datatype == "str":
            return str(val)
        return None

    def niceresult(self):
        if self.result == None:
            return "NA";
        return self.convert_to_reqtype(self.result)

class Collector_SqlDerivedData(CollectorBase):
    def __init__(self,name,shortname,datatype,mtype,par = {}):
        global databases
        super().__init__(name,shortname,datatype,mtype,par)
        self._sd = databases[par["database_ref"]]
        self._sql = par["sql"]

    def read_low(self,sec):
        self._measured = None
        self._sd.connect()
        self._measured = self._sd.execute_onevalue_sql(self._sql)
        self._sd.disconnect()
        return self._measured

class CollectorBase_ReadFileBased(CollectorBase):
    def __init__(self,name,shortname,datatype,mtype,par = {}):
        super().__init__(name,shortname,datatype,mtype,par)
        self._filename = par["filename"]

    def read_file(self):
        fp = open(self._filename,"r")
        content = fp.read()
        fp.close()
        return content;

class Collector_ReadFileData(CollectorBase_ReadFileBased):
    def __init__(self,name,shortname,datatype,mtype,par = {}):
        par["filename"] = par["file"]
        super().__init__(name,shortname,datatype,mtype,par)

    def read_low(self,sec):
        self._measured = self.convert_to_reqtype(self.read_file())
        return self._measured

class Collector_BandwidthBySys(CollectorBase_ReadFileBased):
    def __init__(self,name,shortname,datatype,mtype,par = {}):
        if (par["direction"] != "tx" and par["direction"] != "rx"):
            raise ValueError("BandwidthBySys datasource: direction error")
        par["filename"] = "/sys/class/net/" + par["interface"] + \
                          "/statistics/" + par["direction"] + "_bytes"
        mtype = "increment_in_sec"
        super().__init__(name,shortname,datatype,mtype,par)

    def read_low(self,sec):
        self._measured = float(self.read_file()) / 1024
        return self._measured

    def niceresult(self):
        if self.result == None:
            return "NA";
        return "{0:.2f} kB/s".format(self.result)

class Collector_NetworkPacketsBySys(CollectorBase_ReadFileBased):
    def __init__(self,name,shortname,datatype,mtype,par = {}):
        if (par["direction"] != "tx" and par["direction"] != "rx"):
            raise ValueError("BandwidthBySys datasource: direction error")
        par["filename"] = "/sys/class/net/" + par["interface"] + \
                          "/statistics/" + par["direction"] + "_packets"
        mtype = "increment_in_sec"
        super().__init__(name,shortname,datatype,mtype,par)

    def read_low(self,sec):
        self._measured = float(self.read_file())
        return self._measured

    def niceresult(self):
        if self.result == None:
            return "NA";
        return "{} packets/s".format(self.result)

class Collector_SystemLoadByProc(CollectorBase_ReadFileBased):
    def __init__(self,name,shortname,datatype,mtype,par = {}):
        par["filename"] = "/proc/loadavg"
        super().__init__(name,shortname,datatype,mtype,par)
        self._needed_part_index = 0
        if "index" in par:
            if (int(par["index"]) >= 0 and int(par["index"]) < 3):
                self._needed_part_index = par["index"]
            else:
                raise ValueError("SystemLoadByProc datasource: index error")

    def read_low(self,sec):
        content = self.read_file()
        parts = content.split(" ")
        self._measured = parts[self._needed_part_index]
        return self._measured

class Collector_SystemMemoryByProc(CollectorBase_ReadFileBased):
    def __init__(self,name,shortname,datatype,mtype,par = {}):
        par["filename"] = "/proc/meminfo"
        super().__init__(name,shortname,datatype,mtype,par)
        self._needed_part_str = "MemFree"
        if "reqdata" in par:
            if par["reqdata"] in ["MemTotal","MemFree","MemAvailable","Active","Inactive"]:
                self._needed_part_str = par["reqdata"]
            else:
                raise ValueError("SystemMemoryByProc datasource: unknown data requested error")

    def read_low(self,sec):
        content = self.read_file()
        lines = content.splitlines()
        for line in lines:
            if self._needed_part_str in str(line):
                parts = line.split(":");
                self._measured = int(parts[1].replace(" kB","").strip()) / 1024
                break
        return self._measured

    def niceresult(self):
        if self.result == None:
            return "NA";
        return "{0:.2f} MB".format(self.result)

class Collector_CallSystemCommand(CollectorBase):
    def __init__(self,name,shortname,datatype,mtype,par = {}):
        super().__init__(name,shortname,datatype,mtype,par)
        self._command = par["command"]

    def read_low(self,sec):
        r = os.popen(self._command).read().strip()
        self._measured = self.convert_to_reqtype(r)
        return self._measured

# ##############################################################################

class PublisherBase:
    def __init__(self,par = {}):
        self._target = "stdout"
        self._mode = 0
        if "outputfile" in par:
            self._target = "file"
            self._filename = par["outputfile"]
            self._mode = 1
        if "appendfile" in par:
            self._target = "file"
            self._filename = par["appendfile"]
            self._mode = 2

    def generate_string(self,mlist):
        pass

    def publish(self,mlist):
        pass

class PublisherBase_StdOut(PublisherBase):
    def __init__(self,par = {}):
        super().__init__(par)
        self._target = "stdout"
        self._mode = 0
        if "outputfile" in par:
            self._target = "file"
            self._filename = par["outputfile"]
            self._mode = 1
        if "appendfile" in par:
            self._target = "file"
            self._filename = par["appendfile"]
            self._mode = 2

    def publish(self,mlist):
        output = self.generate_string(mlist)
        if self._target == "stdout":
            print(output)
        if self._target == "file":
            om = "w"
            if self._mode == 2:
                om = "a"
            of = open(self._filename,om)
            of.write(output)
            of.close()

class Publisher_SqlDatabase(PublisherBase):
    def __init__(self,par = {}):
        global databases
        super().__init__(par)
        self._dbref = databases[par["database_ref"]]
        self._tablename = par["tablename"]
        self._timestampname = par["timestampname"]
        self._sql = ""
        self._sqlpars = []

    def generate_string(self,mlist):
        self._sql = "insert into "+self._tablename+"("+self._timestampname
        self._sqlpars = []
        for m in mlist:
            if len(m.shortname) > 0 and m.result != None:
                self._sql += "," + m.shortname
        self._sql += ") VALUES(" + self._dbref.current_timestamp_string()
        for m in mlist:
            if len(m.shortname) > 0 and m.result != None:
                self._sql += ",%s"
                self._sqlpars.append(m.convert_to_reqtype(m.result))
        self._sql += ")"

    def publish(self,mlist):
        self.generate_string(mlist)
        self._dbref.connect()
        self._dbref.execute_noresult_sql(self._sql,self._sqlpars)
        self._dbref.disconnect()

class Publisher_NiceTable(PublisherBase_StdOut):
    def __init(self,mlist,par = {}):
        super().__init__(mlist,par)

    def generate_string(self,mlist):
        out = "---------- start ----------\n"
        out += str(datetime.datetime.now()) + "\n"
        for m in mlist:
            out += "{0} = {1}\n".format(m.name,m.niceresult())
        out += "---------- end ----------\n"
        return out

class Publisher_Json(PublisherBase_StdOut):
    def __init(self,mlist,par = {}):
        super().__init__(mlist,par)

    def generate_string(self,mlist):
        o = {}
        for m in mlist:
            if len(m.shortname) > 0 and m.result != None:
                o[m.shortname] = m.convert_to_reqtype(m.result)
        return json.dumps(o)

# ##############################################################################

class Controller:
    def __init__(self):
        self.measure_interval_sec = 5
        self.last_wait_sec = 0
        self.metering_agent_runmode = "once"
        self._ms = []
        self._ps = []
        self._condi = []

    def add_measurement(self,m : CollectorBase):
        self._ms.append(m)

    def add_publisher(self,p : PublisherBase):
        self._ps.append(p)

    def add_cond_interval(self,conditerval):
        self._condi.append(conditerval)

    def read_all(self):
        for m in self._ms:
            m.read(self.last_wait_sec)

    def publish_all(self):
        for p in self._ps:
            p.publish(self._ms)

    def wait(self):
        c_pri = 0
        self.last_wait_sec = self.measure_interval_sec
        n = datetime.datetime.now()
        hour = int(n.strftime("%H"))
        weekd = str(n.strftime("%a")).lower()
        for ci in self._condi:
            if ci['dow'] != None and ci['dow'] != weekd:
                continue
            if ci['fh'] != None and hour < ci['fh']:
                continue
            if ci['uh'] != None and hour > ci['uh']:
                continue
            if c_pri > ci["pri"]:
                continue
            c_pri = ci["pri"]
            self.last_wait_sec = ci["int"]
        sleep(self.last_wait_sec)

dyn_loaded_classes = {}
def get_class(name,category):
    classprefix = ""
    if category == "database":
        classprefix = "DatabaseConnection_"
    if category == "coll":
        classprefix = "Collector_"
    if category == "pub":
        classprefix = "Publisher_"

    if (classprefix+name) in globals():
        return globals()[classprefix+name]
    else:
        if classprefix+name in dyn_loaded_classes:
            return dyn_loaded_classes[classprefix+name]
        filename = "plugin_" + name + ".py"
        if os.path.isfile(filename):
            im = SourceFileLoader("module."+name,filename).load_module()
            clss = getattr(im,classprefix + name,None)
            if clss == None:
                raise ValueError('Error, cannot load class "' + classprefix + name + '"')
            dyn_loaded_classes[classprefix+name] = clss;
        else:
            raise ValueError('Error, cannot load file "'+ filename + '"')
        return clss

def loadconfig(controller,config):
    if "config" in config:
        if "databases" in config["config"]:
            if len(config["config"]["databases"]) > 0:
                for db in config["config"]["databases"]:
                    clss = get_class(db["type"],"database")
                    databases[db["refname"]] = clss(db["host"],db["name"],db["user"],db["password"])

        if "collector" in config["config"]:
            if "interval" in config["config"]["collector"]:
                controller.measure_interval_sec = int(config["config"]["collector"]["interval"])
            controller.metering_agent_runmode = config["config"]["collector"]["runmode"]
            if controller.metering_agent_runmode not in ["once","twice","loop"]:
                raise ValueError('Error, unknown run mode "'+controller.metering_agent_runmode+'"')
            if "cond_intervals" in config["config"]["collector"]:
                if len(config["config"]["collector"]["cond_intervals"]) > 0:
                    for ci in config["config"]["collector"]["cond_intervals"]:
                        cin = {"n":"","dow":None,"fh":None,"uh":None,"pri":1,"int":None}
                        if "name" in ci:
                            cin["n"] = ci["name"]
                        if "dayofweek" in ci:
                            cin["dow"] = str(ci["dayofweek"]).lower()
                        if "from_h" in ci:
                            cin["fh"] = int(ci["from_h"])
                        if "until_h" in ci:
                            cin["uh"] = int(ci["until_h"])
                        if "priority" in ci:
                            cin["pri"] = ci["priority"]
                        cin["int"] = ci["interval"]
                        controller.add_cond_interval(cin)

        if "inputs" in config["config"]:
            if len(config["config"]["inputs"]) > 0:
                for src in config["config"]["inputs"]:
                    name = src["name"]
                    shortname = src["shortname"]
                    typestr = src["type"]
                    mtype = "passthroug"
                    if "calcmode" in src:
                        mtype = src["calcmode"]
                    if "agent" in src:
                        if "agentname" in src["agent"]:
                            collector = src["agent"]["agentname"]
                            clss = get_class(collector,"coll")
                            new_src = clss(name,shortname,typestr,mtype,src["agent"])
                            controller.add_measurement(new_src)

        if "outputs" in config["config"]:
            if len(config["config"]["outputs"]) > 0:
                for out in config["config"]["outputs"]:
                    otype = out["type"]
                    clss = get_class(otype,"pub")
                    new_out = clss(out)
                    controller.add_publisher(new_out)

def main():
    pkg_resources.require(dependencies)

    if len(sys.argv) < 2:
        print("Error: the required parameter missing.\n")
        exit(1)

    controller = Controller()

    with open(sys.argv[1]) as file:
        c = yaml.load(file, Loader=yaml.FullLoader)
        loadconfig(controller,c)
        del c

    if controller.metering_agent_runmode == "once":
        controller.read_all()
        controller.publish_all()

    if controller.metering_agent_runmode == "twice":
        controller.read_all()
        controller.wait()
        controller.read_all()
        controller.publish_all()

    if controller.metering_agent_runmode == "loop":
        while True:
            controller.read_all()
            controller.publish_all()
            controller.wait()

if __name__ == '__main__':
    main()
