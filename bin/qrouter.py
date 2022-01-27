#!/usr/bin/env python3
"""
check subdir under ~/sendxms/SERVER_SUPER100/received/
for each subdir process batch files
for each file
1. check routing and move file to output SMPP folder
2. insert into cdr (insert to redis, insert_cdr_from_redis_cache.py will take care insertion)
"""

import time
import os
import signal
import sys
from pathlib import Path
import logging
import re
import random
import site

basedir = os.path.abspath(os.path.dirname(__file__))
libdir = os.path.join(basedir, "../pylib")
site.addsitedir(libdir)
import DB

mode = ''
try:
    mode = sys.argv[1]
except:
    pass

instance = os.path.basename(__file__).split(".")[0]
log = os.path.join(basedir, f"../log/{instance}.log")
lockfile= os.path.join(basedir, f"../var/lock/{instance}.lock")
config_file = os.path.join(basedir, "../etc/router.cfg")

print(log,lockfile,config_file)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.Formatter.converter = time.gmtime
# create a file handler
handler = logging.FileHandler(log)
handler.setLevel(logging.INFO)
# create a logging format
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
# add the handler to the logger
logger.addHandler(handler)

### global variable
db = DB.connectdb()
if not db:
    logger.warning("!!! cannot connect to PostgreSQL DB")
    exit()
cur = db.cursor()

r = DB.connect_redis()
if not r:
    logger.warning("!!! cannot connect to redis server")
    exit()

batch = 2

#### regex to match key info in SMS file
r_bnumber = re.compile("^Phone=(.*)")
r_xms = re.compile("^XMS=(.*)")
r_tpoa = re.compile("^OriginatingAddress=(.*)")
r_udh = re.compile("^UDH=(.*)")
r_msgid = re.compile("^MsgId=(.*)")
r_dcs = re.compile("^DCS=(.*)")
r_split = re.compile("^Split=(.*)")
r_server = re.compile("SERVER")
r_action = re.compile("^Action=(.*)")

#### regex to clean bnumber
r_leading_plus = re.compile(r'^\++')
r_leading_zero = re.compile(r'^0+')

r_msisdn = re.compile("^\+?\d+$")

#### regex to match OPTOUT MO, get out keyword if there is any, keyword is to identify brand
#r_optout_with_keyword = re.compile(r'(optout|stop)\s+(\w+)',re.IGNORECASE)
#r_optout = re.compile(r'(optout|stop)',re.IGNORECASE)

d_ac = dict() #keep smpp_account info

def read_config():

    logger.info("======= read_config =======")
    sql = f"select id,name,billing_id,directory,product_id from smpp_account where live=1;"
    cur.execute(sql)
    rows = cur.fetchall()
    if rows:
        d_ac.clear() #reset d_ac

        for row in rows:
            (acid,acname,billing_id,in_dir,product_id) = row
            d_ac[acid] = f"{acname}---{billing_id}---{in_dir}---{product_id}"

    for k,v in d_ac.items():
        logger.info(f"{k} => {v}")
    
    logger.info("===============================")

def clean_bnumber(bnumber):
    bnumber = re.sub(r_leading_plus, r'',bnumber)
    bnumber = re.sub(r_leading_zero, r'',bnumber)
    bnumber = re.sub(r'^',r'+',bnumber)
    return bnumber

def leave(signal, frame): #INT, TERM
    logger.info(f"received signal {signal}, will exit")
    os.unlink(lockfile)
    cur.close()
    db.close()
    sys.exit()

def reload_config(signal, frame): #USR1
    logger.info(f"### received signal {signal}, reload_config")
    read_config()
    return

def save_sql(sql):
    logger.info(sql)
    if r.lpush('cdr_cache',sql): #successful transaction return True
        logger.info(f"LPUSH cdr_cache OK")
    else:
        logger.warning(f"!!! problem to LPUSH cdr_cache {sql}")

def scandir(acid):
    acname,billing_id,in_dir,product_id = d_ac.get(acid).split("---")
    e = Path(in_dir)
    logger.info(f"scan dir for {acname}({acid}) {in_dir}")

    count = 0
    for myfile in e.iterdir():
        if count > batch:
            logger.info(f"processed {count} file for {acname}({acid}) {in_dir}")
            break
        #if myfile.is_file() and re.match("^xms",os.path.basename(myfile)):
        if os.path.getsize(myfile) > 0 and re.match("^xms",os.path.basename(myfile)):
            if process_file(myfile,acid):
                count += 1 

def get_route():
    ###smsc_id---provider_id---output_dir
    return "1---6---/home/xqy/dev/python3/fastapi/httpapi/sendxms/CMI_PREMIUM1/spool/CMI_PREMIUM1"

def process_file(myfile,acid):
    acname,billing_id,in_dir,product_id = d_ac.get(acid).split("---")

    bnumber,msgid,xms,tpoa,udh,action = '','','','','',''
    dcs,error,split = 0,0,1
    route = ''
    to_trash= 0
    tpoa_status = 2000 
    insert_optout = 0 #if it's optout MO, insert into table optout, so future Promotion should not be sent to this number
    logger.info(f"process_file: {myfile} for acid {acid}")
    logger.info("=========================")
    with open(myfile,'r') as reader:
        for line in reader: #same as: for line in reader.readlines():
            line = line.strip()
            logger.info(line)
            if(z := r_xms.match(line)):
                xms = z.groups()[0]
            elif(z := r_bnumber.match(line)):
                bnumber = z.groups()[0]
            elif(z := r_tpoa.match(line)):
                tpoa = z.groups()[0]
                tpoa = re.sub(r'^\d:\d:',r'',tpoa).strip() # 5:0:Routee => Routee
            elif(z := r_udh.match(line)):
                udh = z.groups()[0]
            elif(z := r_dcs.match(line)):
                dcs = z.groups()[0]
            elif(z := r_msgid.match(line)):
                msgid = z.groups()[0]
            elif(z := r_split.match(line)):
                split = z.groups()[0]
            elif(z := r_action.match(line)):
                action = z.groups()[0]

    logger.info("=========================")

    bnumber = clean_bnumber(bnumber)
 
    xms_len = len(xms)

    #### trash DLR , only process MO
    if action == 'Status':
        logger.info(f"!!! trash DLR")
        to_trash = 1
    else:
        route = get_route()

    smscid,providerid,outdir = route.split('---')
    smscid = int(smscid)
    providerid = int(providerid)
    outfile = os.path.join(outdir, os.path.basename(myfile))
    tmpdir = outdir + '/tmp'
    tmpsms = tmpdir + '/' + os.path.basename(myfile) 
    
    if to_trash == 1:
        os.rename(myfile,outfile)
        logger.info(f"trash {myfile} to {outfile}")

    else: #to_trash == 0
        os.remove(myfile)
        logger.info(f"delete input file {myfile}")
    
        msg_submit = f"""\
; encoding=UTF-8
[{acname.upper()}]
Phone={bnumber}
OriginatingAddress={tpoa}
Priority=0
XMS={xms}
DCS={dcs}
LocalId={msgid}
StatusReportRequest=1
"""
        if int(split) > 1:
            msg_submit += f"Split={split}\n"
        if udh != '':
            msg_submit += f"UDH={udh}\n"

        with open(tmpsms,'w') as w:
            w.write(msg_submit)
        logger.info("=========================")
        logger.info(msg_submit)
        logger.info("=========================")

        os.rename(tmpsms,outfile)
        logger.info(f"rename {tmpsms} to {outfile}")

        #result = DB.parse_bnumber(np, tpoa)
        result = "3---408"
        if result == None:
            logger.info(f"!!! parse_bnumber can not find {bnumber} belong to which network")
        else:
            cid,opid = result.split('---')
        
        #treat single quote before inserting to postgresql
        tpoa = re.sub("'","''",tpoa)
        xms = re.sub("'","''",xms)
        xms = xms[:400]
  
        save_sql(f"insert into cdr (msgid,billing_id,product_id,smpp_account_id,tpoa,bnumber,country_id,operator_id,dcs,len,split,udh,provider_id,smsc_id,xms) values ('{msgid}',{billing_id},{product_id},{acid},'{tpoa}','{bnumber}',{cid},{opid},{dcs},{xms_len},{split},'{udh}',{providerid},{smscid},'{xms}')")

    return 1
    
def check_pid_running(pid):
    '''Check For the existence of a unix pid.
    '''
    try:
        os.kill(pid,0)
    except OSError:
        return False
    else:
        return True
        
def main():
    pid = os.getpid()
    logger.info(f"Hey, {__file__} (pid: {pid}) is started!")

    try:
        with open(lockfile,'r') as f:
            oldpid = f.readline()
            oldpid.strip() #chomp
            if oldpid != '':
                while check_pid_running(int(oldpid)): #check_pid_running return true, means the process is running
                    logger.info(f"###### {__file__} {oldpid} is running, kill it and run my own")
                    os.kill(int(oldpid), signal.SIGTERM)
                    time.sleep(5)
    except FileNotFoundError:
        logger.info("no lock file, will create one")


    with open(lockfile,'w') as w:
        logger.info(f"create lockfile {lockfile}: {pid}")
        w.write(str(pid))
        
    signal.signal(signal.SIGINT, leave)
    signal.signal(signal.SIGTERM, leave)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGUSR1, reload_config)

    read_config()

    ##### print out config #### 
    last_print = time.time()
    while True:
        show_alive = 0
        now = time.time()
        if now - last_print > 10: #every 10 sec, print something to show alive
            show_alive = 1
            last_print = now

        if ext == 'http':
            ### get sms from redis
            pass
        else:
            for acid,info in d_ac.items():
                if show_alive == 1:
                    logger.info(f"scandir for acid {acid}")
                scandir(acid)
            
            time.sleep(1)

#       signal.pause()

if __name__ == '__main__':
    main()

