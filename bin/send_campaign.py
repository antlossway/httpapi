#!/usr/bin/env python3

import requests
import json
import psycopg2 as pg
import time
import sys
import threading
from collections import defaultdict
import os
from concurrent.futures import ThreadPoolExecutor
import logging
import signal
from itertools import repeat
import re
from configparser import ConfigParser

import site
basedir = os.path.abspath(os.path.dirname(__file__))
libdir = os.path.join(basedir, "../pylib")
site.addsitedir(libdir)
import DB

def read_config(cfg):
    config = ConfigParser()
    config.read(cfg)
    
    return config

#####################
## global variable ##
#####################
log_dir = os.path.join(basedir, "../log/")
lock_dir = os.path.join(basedir, "../var/lock/")

log = log_dir + "send_campaign.log"
lockfile= lock_dir + 'send_campaign.lock'

num_thread = 5 

cfg = os.path.join(basedir, "../etc/config.txt")
config = read_config(cfg)
master_key = config['api_test']['api_key']
master_secret = config['api_test']['api_secret']

#####################
## log configuration 
#####################

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

try:
    db = DB.connectdb()
    cur = db.cursor()
    logger.info("postgreSQL DB connected")
except Exception as error:
    logger.warning(f"!!! DB connection failed: {error}")
    exit()

numbering_plan = DB.get_numbering_plan(cur)
logger.info(f"### get_numbering_plan: {len(numbering_plan)} entries")
logger.info(f"master_key: {master_key}, master_secret: {master_secret}")

def check_pid_running(pid):
    try:
        os.kill(pid,0)
    except OSError:
        return False
    else:
        return True

def leave(signal, frame): #INT, TERM
    logger.info(f"!!! receive signal {signal}, will exit")
    os.unlink(lockfile)

    sys.exit()
 
def get_cpg_list(cur,cpg_id):
    sql = f"""select hash,field_name,value from cpg_blast_list where cpg_id={cpg_id}"""
    logger.info(sql)
    d = defaultdict(dict)
    cur.execute(sql)
    rows = cur.fetchall()
    for row in rows:
        (md5,field,value) = row
        d[md5][field] = value

    return d
        
def send_sms(d, d_ac): #d: dict {'number':'6512355566','var1':'variable'}, d_ac: account related info {'billing_id':xxx, 'product_id':xxx}
    tid = str(threading.get_ident()) #thread id

    logger.info(f"[{tid}]: process SMS {json.dumps(d,indent=4)}, {json.dumps(d_ac,indent=4)}")

    #### get bnumber
    bnumber = d.get('number',None)
    if not bnumber: #not supposed to happen
        logger.warning(f"!!! no bnumber found")
        return None

    ### get country_id, operator_id
    bnumber = DB.clean_msisdn(bnumber)
    parse_result = DB.parse_bnumber(numbering_plan,bnumber)
    if parse_result:
        country_id,operator_id = parse_result.split('---')
    else:
        logger.warning(f"!!! unknown destination {bnumber}")
        return None
    
    logger.info(f"bnumber {bnumber}, cid {country_id}, opid {operator_id}")

    ### TBD: find in custome_operator_routing which provider_id to use, and use that provider's connector to send SMS
    # provider_id = get_provider_id(product_id,country_id,operator_id)
    #ep = f"https://{master_key}:{master_secret}@dev1.ameex-mobile.com/api/sms"

    ep = f"http://{master_key}:{master_secret}@localhost:8000/api/internal/sms" # call our own post SMS API, which will handle routing and insert cdr
 
    #del d['number']
    ### replace content template variable if there is any
    xms = d_ac.get('xms')
    sender = d_ac.get('sender')
    for field,value in d.items():
        pattern = f"%{field}%"
        try:
            xms = re.sub(pattern, value, xms)
        except Exception as err:
            logger.warning(f"!!! {err}")
    logger.info(f"final SMS content: {xms}")

    data = {
        "from": sender,
        "to": bnumber,
        "content": xms,
        "cpg_id": d_ac.get("cpg_id"),
        "account": {
            "billing_id": d_ac.get("billing_id"),
            "webuser_id": d_ac.get("webuser_id"),
            "product_id": d_ac.get("product_id"),
            "api_credential_id": d_ac.get("api_credential_id")
        }
    }

    resp = requests.post(ep,json=data, timeout=(2,10))
    #resp = requests.post(ep, json=data,verify=True)
    logger.info("### post SMS request:")
    logger.info(json.dumps(data,indent=4))

    res_json = resp.json()
    
    logger.info(f"json.dumps(res_json): {json.dumps(res_json,indent=4)}")

    if resp.ok:
        logger.info(f"resp.text: {resp.text}")
    else:
        logger.warning("!!! NOK")
        logger.info(resp.raise_for_status())


def main():
    pid = os.getpid()
    logger.info(f"Hey, {__file__} (pid {pid} is started!")
    
    try:
        with open(lockfile, 'r') as f:
            oldpid = f.readline().strip()
            if oldpid != '':
                while check_pid_running(int(oldpid)):
                    logger.info("!!! program is running, kill it and run new one")
                    os.kill(int(oldpid), signal.SIGTERM)
                    time.sleep(5)
    except FileNotFoundError:
        logger.info(f"!!! no lock file {lockfile}, will create one")

    with open(lockfile, 'w') as w:
        logger.info(f"create lock file {lockfile}: {pid}")
        w.write(str(pid))

    signal.signal(signal.SIGINT, leave)
    signal.signal(signal.SIGTERM, leave)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
 
    while True:
        count = 0

        cpg_id = None
        ### select campaign
        cur.execute("select id,tpoa,xms,billing_id,webuser_id,product_id,api_credential_id from cpg where status = 'TO_SEND' and sending_time < current_timestamp limit 1;")
        try:
            (cpg_id,sender,xms,billing_id,webuser_id,product_id,api_credential_id) = cur.fetchone()
        except:
            pass
        
        if cpg_id:
            d_ac = {
                'billing_id': billing_id,
                'webuser_id': webuser_id,
                'product_id': product_id,
                'api_credential_id': api_credential_id,
                'sender':sender,
                'xms': xms,
                'cpg_id': cpg_id
            }
       
            cur.execute(f"update cpg set status = 'SENDING' where id = {cpg_id}")
    
            #### get B-number list
            data = get_cpg_list(cur,cpg_id) #dict: md5 => {'number':'12355','var':'variable'}
    
            ### build a list of dict to be passed to multithread
            l_d = list() #list of dict to hold bnumber and other variables for a SMS
            for md5, d in data.items():
                l_d.append(d)
                count += 1
                
            if count > 0:
                logger.info(f"cpg_id {cpg_id} has {count} bnumber")

                start_time = time.time()
        
                with ThreadPoolExecutor(num_thread) as executor:
                    executor.map(send_sms, l_d, repeat(d_ac)) #for the same campaign, d_ac hold account related info, which is the same, so "repeat"
                    ## TBD: how to catch exception in the thread?
         
                end_time = time.time()
                duration = int(end_time - start_time)
                logger.info(f"duration: {duration}")

                cur.execute(f"update cpg set status = 'SENT' where id = {cpg_id}")
            else:
                logger.warning(f"!!! no blast list found for cpg_id {cpg_id}")
        if count == 0:
            logger.info("Keep Alive")
        
        time.sleep(30)


if __name__ == '__main__':
    main()
