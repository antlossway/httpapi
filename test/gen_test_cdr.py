#!/usr/bin/env python3
import random
import string
from datetime import datetime
from uuid import uuid4
import re
import time
import sys
from configparser import ConfigParser
import os
import psycopg2

basedir = os.path.abspath(os.path.dirname(__file__)) + "/../"
config_dir = basedir + "config/"
config_file = basedir + '.config'

def read_config():
    config = ConfigParser()

    ### reinitialize
    for section in config.sections():
        config.remove_section(section)

    config.read(config_file)

    for section in config.sections():
        print(f"#### {section} ####")
        for key,value in config[section].items():
            print(f"{key} => {value}")

    return config

config = read_config()
db_host = config['postgresql']['host']
db_name = config['postgresql']['db']
db_user = config['postgresql']['user']
db_pass = config['postgresql']['password']

try:
    db = psycopg2.connect(host=db_host,database=db_name, user=db_user, password=db_pass)
    cur = db.cursor()
except Exception as error:
    print (f"!!! DB connection failed: {error}")
    exit()

def gen_code(length=5): #default return random 5-digits
    digits = string.digits
    return ''.join( [ random.choice(digits) for n in range(length) ] )

def gen_bnumber(length=10): #default generate 10 digits
    bnumber = base_bnumber + gen_code(length)
    return bnumber

def gen_content():
    base = random.choice(content_template)
    code = gen_code()
    xms = re.sub(r'<ID>', code, base)  # replace <ID> with random pin code
    return xms

def gen_random_timestamp():
    now_epoch = time.time()
    random_epoch = time.time() - random.randint(0, 86400*10) #past 10 days
    time_obj = time.gmtime(random_epoch) #struct_time
    time_str = time.strftime("%Y-%m-%d, %H:%M:%S", time_obj)
    return time_str

### get account_id, billing_id, product_id
l_acid = list()
d_ac = dict()
l_provider_id = list()

sql = "select id,billing_id,product_id from account;"
cur.execute(sql)
rows = cur.fetchall()
for row in rows:
    (account_id,billing_id,product_id) = row
    l_acid.append(account_id)
    d_ac[account_id] = f"{billing_id}---{product_id}"

sql = "select id,name from provider;"
cur.execute(sql)
rows = cur.fetchall()
for row in rows:
    (id,name) = row
    l_provider_id.append(id)

base_bnumber = "+65"
country_id = 3
l_operator_id = [408,252,578]

l_sender = ['DBS', 'OCBC', 'Sephora', 'Apple']
content_template = ['<ID> is your verfication code', 'PIN CODE: <ID>']

l_status = ['DELIVRD','EXPIRED','REJECTD','UNDELIV','Pending']


def gen_cdr():
    account_id = random.choice(l_acid)
    billing_id,product_id = d_ac.get(account_id).split("---")
    provider_id = random.choice(l_provider_id)
    
    sender = random.choice(l_sender)
    bnumber = gen_bnumber(8)
    msgid = str(uuid4())
    operator_id = random.choices(l_operator_id, weights=[20,30,60])[0]
    xms = gen_content()
    status = random.choices(l_status,weights=[90,1,2,5,2])[0]
    ts = gen_random_timestamp()
    
    if status == 'Pending':
        sql = f"""insert into cdr (dbtime,account_id,billing_id,product_id,msgid,tpoa,bnumber,country_id,operator_id,xms,provider_id) values ('{ts}',{account_id},{billing_id},{product_id},'{msgid}','{sender}','{bnumber}',{country_id},{operator_id}, '{xms}',{provider_id});"""
    else:
        sql = f"""insert into cdr (dbtime,account_id,billing_id,product_id,msgid,tpoa,bnumber,country_id,operator_id,xms,status,provider_id) values ('{ts}',{account_id},{billing_id},{product_id}, '{msgid}','{sender}','{bnumber}',{country_id},{operator_id}, '{xms}','{status}', {provider_id});"""

    print(sql)

if __name__ == '__main__':
    num = int(sys.argv[1])
    for i in range(num):
        gen_cdr()
