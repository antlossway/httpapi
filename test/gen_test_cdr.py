#!/usr/bin/evn python3
import random
import string
from datetime import datetime
from uuid import uuid4
import re
import time
import sys

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

base_bnumber = "+65"
country_id = 3
l_operator_id = [408,252,578]

l_sender = ['DBS', 'OCBC', 'Sephora', 'Apple']
content_template = ['<ID> is your verfication code', 'PIN CODE: <ID>']

l_status = ['DELIVRD','EXPIRED','REJECTD','UNDELIV']

l_webuser_id = [2,3]
l_product_id = [0,1]

d_map_product_provider = {
    0: 1, #premium => AMEEX_PREMIUM
    1: 5 #standard => AMEEX_STD
}

d_map_webuser_id_api_id = {
    2: 1,
    3: 4 
}

billing_id = 1


def gen_cdr():
    webuser_id = random.choice(l_webuser_id)
    product_id = random.choices(l_product_id, weights=[80,20])[0]
    #print(f"product_id: {product_id}")
    provider_id = d_map_product_provider.get(product_id)
    
    sender = random.choice(l_sender)
    bnumber = gen_bnumber(8)
    msgid = str(uuid4())
    operator_id = random.choices(l_operator_id, weights=[20,30,60])[0]
    xms = gen_content()
    status = random.choices(l_status,weights=[90,1,2,7])[0]
    ts = gen_random_timestamp()
    
    sql = f"""insert into cdr (dbtime,webuser_id,billing_id,product_id,msgid,tpoa,bnumber,country_id,operator_id,xms,status,provider_id) values ('{ts}',{webuser_id},{billing_id},{product_id}, '{msgid}','{sender}','{bnumber}',{country_id},{operator_id}, '{xms}','{status}', {provider_id});"""
    
    print(sql)

if __name__ == '__main__':
    num = int(sys.argv[1])
    for i in range(num):
        gen_cdr()
