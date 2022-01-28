#from textwrap import indent
#from typing import Optional,List
#from pydantic import BaseModel, Field
#from subprocess import call

from textwrap import indent
from fastapi import FastAPI, Body, Response, HTTPException, Depends, Request
from fastapi.responses import JSONResponse

import re
import smsutil
import random
from uuid import uuid4
import json
from email_validator import validate_email
from collections import defaultdict
import os

#import myutils
from myutils import logger, read_comma_sep_lines, gen_udh_base, gen_udh, generate_otp
import mysms
#from mysms import create_sms
from mydb import cur,r,g_account,g_numbering_plan
#import httpapi.myauth as myauth => does not work, saying there is no package httpapi
import myauth
import models

########################
### Global Variables ###
########################

min_len_msisdn = 10 #without prefix like 00 or +
max_len_msisdn = 15 #ITU-T recommendation E.164
max_len_tpoa = 11

redis_status_expire = 15*24*3600 # STATUS:<msgid1> => <status> for /sms/:msgid query_dlr

app = FastAPI(docs_url='/api/docs', 
            redoc_url='/api/redoc',
            openapi_url='/api/openapi.json'

)

    
def is_empty(field):
    if field == '' or field == None:
        return True
    return False

@app.get('/')
async def home():
    return {'result': 'hello'}

#@app.post('/sms', status_code=201)
@app.post('/api/sms', response_model=models.SMSResponse, responses=mysms.example_create_sms_response)
#async def post_sms(response: Response,
async def post_sms(request: Request,
                arg_sms: models.SMS = Body(
                    ...,
                    examples=mysms.example_create_sms,
                ),
#account: str = Depends(myauth.myauth_basic) 
account=Depends(myauth.authenticate) # multiple authentication methods, account is dict including many info
):
    logger.info(f"{request.url.path}: from {request.client.host}")
    d_sms = arg_sms.dict()

    logger.info(f"debug post body")
    for k,v in d_sms.items():
        logger.info(f"{k}: {v}({type(v)})")
    
    sender = d_sms.get("sender", None) #client may sent "from", which is alias as "sender"
    msisdn = d_sms.get("to", None)
    content = d_sms.get("content", None)
    
    result = {}

    ### missing parameters
    if is_empty(sender) or is_empty(msisdn) or is_empty(content):
        resp_json = {
            "errorcode": 2,
            "errormsg": "missing parameter, please check if you forget 'from','to',or 'content'"
         }
        return JSONResponse(status_code=422, content=resp_json)

    ### msisdn format wrong
    msisdn = mysms.clean_msisdn(msisdn)
    if not msisdn:
        resp_json = {
            "errorcode": 2,
            "errormsg": f"B-number {msisdn} is invalid"
        }
        #raise HTTPException(status_code=422, detail=f"B-number {msisdn} is invalid")
        return JSONResponse(status_code=422, content=resp_json)
    
    ### sender format wrong
    len_sender = len(sender)
    if len_sender > max_len_tpoa:
        resp_json = {
            "errorcode": 4,
            "errormsg": f"TPOA/Sender length should not be more than {max_len_tpoa} characters"
        }
        return JSONResponse(status_code=422, content=resp_json)

    ### check B-number country/operator ###
    parse_result = mysms.parse_bnumber(g_numbering_plan,msisdn)
    if parse_result:
        country_id,operator_id = parse_result.split('---')
    else:
        resp_json = {
            "errorcode": 5,
            "errormsg": f"Receipient number {msisdn} does not belong to any network"
        }
        return JSONResponse(status_code=422, content=resp_json)

    ### optional param
    #base64 = d_sms.get("base64url",0)
    base64 = arg_sms.base64url #default 0, non-zero is considered 1
    require_dlr = arg_sms.status_report_req #default 1
    orig_udh = arg_sms.udh #default None

    if base64 != 0:
        logger.info("##### incoming content is already base64url encoded")

    ### get split info
    sms = smsutil.split(content)
    split = len(sms.parts)
    encoding = sms.encoding

    logger.info(f"counts of SMS: {split}")
    dcs = 0
    if not encoding.startswith('gsm'): #gsm0338 or utf_16_be
        dcs = 8 
    
    udh_base = ''
    udh = ''

    if split > 1:
        udh_base = gen_udh_base()
        logger.debug(f"gen_udh_base: {udh_base}")

    l_resp_msg = list() #list of dict

    for i,part in enumerate(sms.parts):
        xms = part.content
        msgid = str(uuid4())

        resp_msg = {"msgid": msgid, "to": msisdn}
        l_resp_msg.append(resp_msg)

        if orig_udh != None and orig_udh != '':
            udh = orig_udh
            logger.info(f"keep orig UDH {udh}")
        
        #for long sms, our UDH will override orig UDH from client
        if udh_base != '':
            udh = gen_udh(udh_base,split,i+1)
            logger.debug(f"gen_udh: {udh}")

        #errorcode = mysms.create_sms_file(account,sender,msisdn,xms,msgid,dcs,udh,require_dlr)
        
        data = {
            "msgid": msgid,
            "sender": sender,
            "to": msisdn,
            "content": xms,
            "require_dlr": require_dlr,
            "country_id": country_id,
            "operator_id": operator_id,
            "udh": udh
        }
        
        errorcode = mysms.create_sms(account,data,'AMEEX_PREMIUM')

        if errorcode == 0:
            pass
        else: #no need to process remain parts
            resp_json = {
                "errorcode": 6,
                "errormsg": "Internal Server Error, please contact support"
            }
            #raise HTTPException(status_code=500, detail=f"Internal Server Error, please contact support")
            return JSONResponse(status_code=422, content=resp_json)

            break

    resp_json = {
                 'errorcode': errorcode,
                 'message-count': split,
                 'messages': l_resp_msg
                }
    logger.info("### reply client:")
    logger.info(json.dumps(resp_json, indent=4))
 
    #return resp_json
    return JSONResponse(status_code=200, content=resp_json)


@app.post('/api/callback_dlr', include_in_schema=False, status_code=200) # to receive push DLR from providers, don't expose in API docs
async def callback_dlr(arg_dlr: models.CallbackDLR, request: Request):
    logger.info(f"{request.url.path}: from {request.client.host}")
    d_dlr = arg_dlr.dict()
    logger.info("### receive DLR")
    logger.info(json.dumps(d_dlr, indent=4))
    bnumber = arg_dlr.msisdn
    msgid2 = arg_dlr.msgid
    status = arg_dlr.status
    timestamp = arg_dlr.timestamp
    
    bnumber = re.sub(r'^\+','',bnumber) #remove beginning +
    bnumber = re.sub(r'^0+','',bnumber) #remove beginning 0
    bnumber = "+" + bnumber #add back beginning +

    #query redis MSGID2:msgid2 => msgid1:::api_key:::require_dlr
    msg_info = r.get(f"MSGID2:{msgid2}")
    
    if msg_info:
        msg_info = msg_info.decode("utf-8") # result from redis-server is byte, need to convert to str
        logger.info(f"## find mapping msgid1 for msgid2 {msgid2}: {msg_info}")
        msgid1, api_key, require_dlr = msg_info.split(":::")
        if int(require_dlr) == 1:
            ac = g_account.get(api_key)
            callback_url = ac.get('callback_url')
            logger.info(f"will push back DLR to {callback_url}")
            pass # TBD: push back DLR to client
        
        ### notif3: to be processed by notif3update to update status in cdr
        sql = f"insert into notif3 (bnumber,msgid,localid,status) values ('{bnumber}','{msgid2}','{msgid1}','{status}');"
        logger.info(sql)
        cur.execute(sql)

        ### redis: STATUS:msgid1 => status
        k = f"STATUS:{msgid1}"
        r.setex(k, redis_status_expire, value=status)
        logger.info(f"SETEX {k} {redis_status_expire} {status}")

    else:
        logger.warning(f"!!! no msgid1 mapping found for {msgid2}")



@app.post('/api/internal/cpg') #UI get uploaded file from user, call this API to process data, if data valid will create campaign
#async def create_campaign(arg_new_cpg: models.InternalNewCampaign, request: Request, auth_result=Depends(myauth.allowinternal)):
async def create_campaign(
    request: Request,
    arg_new_cpg: models.InternalNewCampaign = Body(
                     ...,
                     examples=models.example_internal_cpg,
    ),
):

    # blast_list: List[str]
    # cpg_name: str
    # cpg_tpoa: str
    # cpg_xms: str
    # billing_id: int
    # account_id: int
    # product_id: int
    logger.info(f"{request.url.path}: from {request.client.host}")
    
    blast_list = arg_new_cpg.blast_list
    l_data = read_comma_sep_lines(blast_list)
    if not l_data: #None, means no valid bnumber
        raise HTTPException(status_code=422, detail=f"no valid entry")
    elif l_data == -1:
        raise HTTPException(status_code=422, detail=f"issue with the format of blast list content")
    else:
        cpg_name = arg_new_cpg.cpg_name
        tpoa = arg_new_cpg.cpg_tpoa
        xms = arg_new_cpg.cpg_xms
        billing_id = arg_new_cpg.billing_id
        account_id = arg_new_cpg.account_id
        product_id = arg_new_cpg.product_id

        cpg_name = re.sub(r"'",r"''",cpg_name)
        tpoa = re.sub(r"'",r"''",tpoa)
        xms = re.sub(r"'",r"''",xms)

        sql = f"""insert into cpg (name,tpoa,billing_id,account_id,product_id,xms) values 
                ('{cpg_name}','{tpoa}',{billing_id},{account_id},{product_id},'{xms}') returning id;"""
        logger.debug(sql)
        cur.execute(sql)
        cpg_id = cur.fetchone()[0]

        for d in l_data:
            hash_value = d.get('hash',None)
            if hash_value:
                del d['hash'] #delete 'hash' from the dict
                for k,v in d.items():
                    sql = f"""insert into cpg_blast_list (cpg_id,field_name,value,hash) values ({cpg_id}, '{k}','{v}','{hash_value}');"""
                    logger.debug(sql)
                    try:
                        cur.execute(sql)
                    except Exception as err:
                        logger.debug(f"!!! insertion error {err}")
        resp_json = {
            'cpg_id': cpg_id,
            'count_valid_entry': len(l_data)
        }

    logger.info("### reply UI:")
    logger.info(json.dumps(resp_json, indent=4))
 
    return JSONResponse(status_code=200, content=resp_json)


whitelist_ip = ['127.0.0.1','localhost','13.214.145.167']
@app.post('/api/internal/sms', response_model=models.SMSResponse, responses=mysms.example_create_sms_response)
#async def post_sms(response: Response,
async def post_sms(arg_sms: models.InternalSMS, request:Request, auth_result=Depends(myauth.allowinternal)):
    logger.info(f"{request.url.path}: from {request.client.host}")
    ### only allow whitelisted IP => move to myauth.allowinternal
    # client_ip = request.client.host
    # if not request.client.host in whitelist_ip:
    #     raise HTTPException(status_code=401, detail=f"unauthorized access")
        
    d_sms = arg_sms.dict()
    logger.info(f"debug post body")
    logger.info(json.dumps(d_sms,indent=4))
    
    sender = arg_sms.sender #client may sent "from", which is alias as "sender"
    msisdn = arg_sms.to
    content = arg_sms.content
    cpg_id = arg_sms.cpg_id

    result = {}

    ### missing parameters
    if is_empty(sender) or is_empty(msisdn) or is_empty(content):
        resp_json = {
            "errorcode": 2,
            "errormsg": "missing parameter, please check if you forget 'from','to',or 'content'"
        }
        return JSONResponse(status_code=422, content=resp_json)

    ### msisdn format wrong
    msisdn = mysms.clean_msisdn(msisdn)
    if not msisdn:
        resp_json = {
            "errorcode": 2,
            "errormsg": f"B-number {msisdn} is invalid"
        }
        return JSONResponse(status_code=422, content=resp_json)
    
    ### sender format wrong
    len_sender = len(sender)
    if len_sender > max_len_tpoa:
        resp_json= {
            "errorcode": 4,
            "errormsg": f"TPOA/Sender length should not be more than {max_len_tpoa} characters"
        }
        return JSONResponse(status_code=422, content=resp_json)

    ### check B-number country/operator ###
    parse_result = mysms.parse_bnumber(g_numbering_plan,msisdn)
    if parse_result:
        country_id,operator_id = parse_result.split('---')
    else:
        resp_json = {
            "errorcode": 5,
            "errormsg": f"Receipient number {msisdn} does not belong to any network"
        }
        return JSONResponse(status_code=422, content=resp_json)

    ### optional param
    require_dlr = 0 # internal call don't need to return DLR
    orig_udh = arg_sms.udh #default None

    ### get split info
    sms = smsutil.split(content)
    split = len(sms.parts)
    encoding = sms.encoding

    logger.info(f"counts of SMS: {split}")
    dcs = 0
    if not encoding.startswith('gsm'): #gsm0338 or utf_16_be
        dcs = 8 
    
    udh_base = ''
    udh = ''

    if split > 1:
        udh_base = gen_udh_base()
        logger.debug(f"gen_udh_base: {udh_base}")

    l_resp_msg = list() #list of dict

    for i,part in enumerate(sms.parts):
        xms = part.content
        msgid = str(uuid4())

        resp_msg = {"msgid": msgid, "to": msisdn}
        l_resp_msg.append(resp_msg)

        if orig_udh != None and orig_udh != '':
            udh = orig_udh
            logger.info(f"keep orig UDH {udh}")
        
        #for long sms, our UDH will override orig UDH from client
        if udh_base != '':
            udh = gen_udh(udh_base,split,i+1)
            logger.debug(f"gen_udh: {udh}")

        #errorcode = mysms.create_sms_file(account,sender,msisdn,xms,msgid,dcs,udh,require_dlr)
        
        data = {
            "msgid": msgid,
            "sender": sender,
            "to": msisdn,
            "content": xms,
            "require_dlr": require_dlr,
            "country_id": country_id,
            "operator_id": operator_id,
            "udh": udh,
            "dcs": dcs,
            "cpg_id": cpg_id
        }
        
        account = arg_sms.account.dict()

        errorcode = mysms.create_sms(account,data,'AMEEX_PREMIUM')

        if errorcode == 0:
            pass
        else: #no need to process remain parts
            resp_json = {
                "errorcode": 6,
                "errormsg": "Internal Server Error, please contact support"
            }
            return JSONResponse(status_code=422, content=resp_json)

            break

    resp_json = {
                 'errorcode': errorcode,
                 'message-count': split,
                 'messages': l_resp_msg
                }
    logger.info("### reply client:")
    logger.info(json.dumps(resp_json, indent=4))
 
    return JSONResponse(status_code=200, content=resp_json)


from werkzeug.security import generate_password_hash,check_password_hash

@app.post('/api/internal/login') #check webuser where deleted=0
async def verify_login(arg_login: models.InternalLogin, request:Request, response:Response):
#async def verify_login(arg_login: models.InternalLogin, request:Request, response:Response, auth_result=Depends(myauth.allowinternal)):
    # check if username exists
    cur.execute("""select u.id as webuser_id,username,password_hash,email,bnumber,role_id,webrole.name as role_name,
    billing_id,b.billing_type,b.company_name,b.company_address,b.country,b.city,b.postal_code,b.currency from webuser u
        left join billing_account b on u.billing_id=b.id left join webrole on u.role_id=webrole.id where username=%s and u.deleted=0
        """, (arg_login.username,))
    row = cur.fetchone()
    if row:
        (webuser_id,username,password_hash,email,bnumber,role_id,role_name,billing_id,billing_type,company_name,company_address,
        country,city,postal_code,currency) = row
        ##verify password
        #if arg_login.password_hash == password_hash:
        if check_password_hash(password_hash,arg_login.password):
            resp_json = {
                "errorcode":0,
                "status":"Success",
                "id":webuser_id,
                "username":username,
                "email":email,
                "bnumber":bnumber,
                "role_id":role_id,
                "role":role_name,
                "billing_id":billing_id,
                "billing_type":billing_type,
                "company_name":company_name,
                "company_address":company_address,
                "country":country,
                "city":city,
                "postal_code":postal_code,
                "currency":currency
            }
        else:
            resp_json = {
                'errorcode': 1,
                'status': "Wrong password!"
            }
            response.status_code = 401

    else:
        resp_json = {
            'errorcode': 1,
            'status': "User not found!"
        }
        response.status_code = 401

    logger.info("### reply internal UI:")
    logger.info(json.dumps(resp_json, indent=4))

    return JSONResponse(status_code=200, content=resp_json)

@app.get("/api/internal/billing/") # get all billing accounts
async def get_all_billing_accounts():
    cur.execute(f"""
    select id,company_name,company_address,country,city,postal_code,contact_name,billing_email,
    contact_number,billing_type,currency,live from billing_account""")

    l_data = list()
    rows = cur.fetchall()
    for row in rows:
        (billing_id,company_name,company_address,country,city,postal_code,contact_name,billing_email,
        contact_number,billing_type,currency,live) = row
        d = {
            "billing_id": billing_id,
            "company_name": company_name,
            "company_address": company_address,
            "country": country,
            "city": city,
            "postal_code": postal_code,
            "contact_name": contact_name,
            "billing_email": billing_email,
            "contact_number": contact_number,
            "billing_type": billing_type,
            "currency": currency,
            "live": live
        }
        l_data.append(d)
    
    resp_json = dict()

    if len(l_data) > 0:
        resp_json = {
            "errorcode":0,
            "status": "Success",
            "count": len(l_data),
            "results": l_data
        }
    else:
        resp_json = {
            "errorcode": 1,
            "status":f"Account Not found"
        }
        return JSONResponse(status_code=404, content=resp_json)
    
    return JSONResponse(status_code=200, content=resp_json)

@app.get("/api/internal/billing/{billing_id}") # get billing account info
async def get_billing_account_info(billing_id: int):
    cur.execute(f"""
    select id,company_name,company_address,country,city,postal_code,contact_name,billing_email,
    contact_number,billing_type,currency,live from billing_account where id=%s""",(billing_id,))

    try:
        row = cur.fetchone()
        (billing_id,company_name,company_address,country,city,postal_code,contact_name,billing_email,
        contact_number,billing_type,currency,live) = row
        resp_json = {
            "billing_id": billing_id,
            "company_name": company_name,
            "company_address": company_address,
            "country": country,
            "city": city,
            "postal_code": postal_code,
            "contact_name": contact_name,
            "billing_email": billing_email,
            "contact_number": contact_number,
            "billing_type": billing_type,
            "currency": currency,
            "live": live
        }
        print(resp_json)
    except:
        resp_json = {
            "errorcode": 1,
            "status":"Users Not found!"
        }
        return JSONResponse(status_code=404, content=resp_json)

    return JSONResponse(status_code=200, content=resp_json)


# use responses to add additional response like returning errors
@app.get("/api/internal/account/{billing_id}") #get all accounts for a billing account
def get_accounts_by_billing_id(billing_id: int, response:Response):
    cur.execute(f"""
    select a.id as account_id,a.name as account_name,a.connection_type,p.name as product_name,systemid,password,api_key,api_secret,
    callback_url,a.comment from account a join product p on a.product_id=p.id where a.deleted=0 and billing_id=%s
    """,(billing_id,))

    l_data = list() #list of dict
    rows = cur.fetchall()
    for row in rows:
        (account_id,account_name,connection_type,product_name,systemid,password,api_key,api_secret,callback_url,comment) = row
        d = {
            "account_id": account_id,
            "account_name": account_name,
            "connction_type": connection_type,
            "product_name": product_name,
            "systemid": systemid,
            "password": password,
            "api_key": api_key,
            "api_secret": api_secret,
            "callback_url": callback_url,
            "comment": comment
        }
        l_data.append(d)
    
    resp_json = dict()

    if len(l_data) > 0:
        resp_json = {
            "errorcode":0,
            "status": "Success",
            "results": l_data
        }
    else:
        resp_json = {
            "errorcode": 1,
            "status":f"Account Not found for billingid {billing_id}"
        }
        return JSONResponse(status_code=404, content=resp_json)
    
    return JSONResponse(status_code=200, content=resp_json)

@app.get("/api/internal/account/")#get all accounts (related to billing accounts)
def get_all_accounts():
    cur.execute(f"""select billing_id,b.company_name,a.id as account_id,a.name as account_name, 
    a.connection_type,p.name as product_name from account a join billing_account b on b.id=a.billing_id 
    join product p on a.product_id = p.id where a.deleted=0;""")

    l_data = list() #list of dict
    rows = cur.fetchall()
    for row in rows:
        (billing_id,company_name,account_id,account_name,connection_type,product_name) = row
        d = {
            "billing_id": billing_id,
            "company_name": company_name,
            "account_id": account_id,
            "account_name": account_name,
            "connection_type": connection_type,
            "product_name": product_name
        }
        l_data.append(d)
    
    resp_json = dict()

    if len(l_data) > 0:
        resp_json = {
            "errorcode":0,
            "status": "Success",
            "results": l_data
        }
    else:
        resp_json = {
            "errorcode": 1,
            "status":"Account Not found!"
        }
        return JSONResponse(status_code=404, content=resp_json)
    
    return JSONResponse(status_code=200, content=resp_json)

@app.get("/api/internal/webuser/")#get all webusers
def get_all_webusers():
    cur.execute(f"""select u.id as webuser_id,u.username,u.email,u.bnumber,b.id as billing_id,b.company_name,u.role_id,r.name as role_name,
    u.live from webuser u join billing_account b on u.billing_id=b.id join webrole r on r.id=u.role_id where u.deleted=0;""")

    l_data = list() #list of dict
    rows = cur.fetchall()
    for row in rows:
        (webuser_id,username,email,bnumber,billing_id,company_name,role_id,role_name,live) = row
        d = {
            "webuser_id": webuser_id,
            "username": username,
            "email": email,
            "bnumber": bnumber,
            "billing_id": billing_id,
            "company_name": company_name,
            "role_id": role_id,
            "role_name": role_name,
            "live": live
        }
        l_data.append(d)
    
    resp_json = dict()

    if len(l_data) > 0:
        resp_json = {
            "errorcode":0,
            "status": "Success",
            "results": l_data
        }
    else:
        resp_json = {
            "errorcode": 1,
            "status":"Webuser Not found!"
        }
        return JSONResponse(status_code=404, content=resp_json)
    
    return JSONResponse(status_code=200, content=resp_json)

@app.get("/api/internal/webuser/{billing_id}")#get all webuser of one billing account
def get_webusers_by_billing_id(billing_id:int):
    cur.execute(f"""select u.id as webuser_id,u.username,u.email,u.bnumber,b.company_name,u.role_id,r.name as role_name,
    u.live from webuser u join billing_account b on u.billing_id=b.id join webrole r on r.id=u.role_id 
    where u.deleted=0 and billing_id=%s;""",(billing_id,))

    l_data = list() #list of dict
    rows = cur.fetchall()
    for row in rows:
        (webuser_id,username,email,bnumber,company_name,role_id,role_name,live) = row
        d = {
            "webuser_id": webuser_id,
            "username": username,
            "email": email,
            "bnumber": bnumber,
            "company_name": company_name,
            "role_id": role_id,
            "role_name": role_name,
            "live": live
        }
        l_data.append(d)
    
    resp_json = dict()

    if len(l_data) > 0:
        resp_json = {
            "errorcode":0,
            "status": "Success",
            "results": l_data
        }
    else:
        resp_json = {
            "errorcode": 1,
            "status":"Webuser Not found!"
        }
        return JSONResponse(status_code=404, content=resp_json)
    
    return JSONResponse(status_code=200, content=resp_json)

def get_userid_from_username(username):
    cur.execute("select id from webuser where username=%s",(username,))
    try:
        webuser_id = cur.fetchone()[0]
        return webuser_id
    except:
        return None

def get_userid_from_email(email):
    cur.execute("select id from webuser where email=%s",(email,))
    try:
        webuser_id = cur.fetchone()[0]
        return webuser_id
    except:
        return None

@app.post("/api/internal/insert", 
#response_model=models.InsertResponse, 
            responses={404: {"errorcode": 1, "status": "some error msg"} }
)
async def insert_record(
    args: models.InternalInsert = Body(
                     ...,
                     examples=models.example_internal_insert,
    ),
    #request: Request
):
    d_args = args.dict()
    logger.debug(f"### orig internal insert request body: {json.dumps(d_args, indent=4)}")

    if not 'table' in d_args:
        resp_json = {
            "errorcode":2,
            "status": f"missing compulsory field table"
        }
        return JSONResponse(status_code=500,content=resp_json)

    table = d_args['table']
    #del d_args['table']

    if table == 'billing_account':
        #company_name and contact name is compulsory
        try:
            data_obj = models.InsertBillingAccount(**args.dict()) #convert into defined model, removing useless field
        except:
            resp_json = {
                "errorcode":2,
                "status": f"missing compulsory field company_name or contact-name"
            }
            return JSONResponse(status_code=500,content=resp_json)
        
        # if billing_email is provided, check if email is valid, comma separated email, will not check uniqueness of email
        if data_obj.billing_email: #email not null
            emails = data_obj.billing_email.split(',')
            for email in emails:
                try:
                    valid = validate_email(email) # return a email object
                except:
                    resp_json = {
                        "errorcode":1,
                        "status": f"Incorrect email address {email}"
                    }
                    return JSONResponse(status_code=422,content=resp_json)
                    break
            
    elif table == 'webuser': 
            ## compulsory field
            # username: str
            # ## optional field
            # password_hash: Optional[str]
            # email: Optional[int]
            # billing_id: Optional[int]
            # role_id: Optional[int]
            # bnumber: Optional[str]     
        try:
            data_obj = models.InsertWebUser(**args.dict()) #convert into defined model, removing useless field
        except:
            resp_json = {
                "errorcode":2,
                "status": f"missing compulsory field"
            }
            return JSONResponse(status_code=500,content=resp_json)
        ### username and email should be unique
        username = data_obj.username
        email = data_obj.email
        if username and get_userid_from_username(username):
            resp_json = {
                "errorcode":2,
                "status": f"username {username} exists"
            }
            return JSONResponse(status_code=403,content=resp_json)
        elif email and get_userid_from_email(email):
            resp_json = {
                "errorcode":2,
                "status": f"email {email} exists"
            }
            return JSONResponse(status_code=403,content=resp_json)

    elif table == 'audit': 
            ## compulsory field
            # billing_id: int
            # webuser_id: int
            # auditlog: st    
        try:
            data_obj = models.InsertAudit(**args.dict()) #convert into defined model, removing useless field
        except:
            resp_json = {
                "errorcode":2,
                "status": f"missing compulsory field"
            }
            return JSONResponse(status_code=500,content=resp_json)
    elif table == 'whitelist_ip': 
            ## compulsory field
            # billing_id: int
            # webuser_id: int
            # ipaddress: str    
        try:
            data_obj = models.InsertWhitelistIP(**args.dict()) #convert into defined model, removing useless field
        except:
            resp_json = {
                "errorcode":2,
                "status": f"missing compulsory field"
            }
            return JSONResponse(status_code=500,content=resp_json)
    elif table == 'account':
        ##compulsory field
        #billing_id: int
        #name: str
        #product_id: int
        #connection_type: smpp/http
        conn_type = d_args.get('connection_type')
        if not conn_type:
            resp_json = {
                "errorcode":2,
                "status": f"missing compulsory field connection_type"
            }
            return JSONResponse(status_code=500,content=resp_json)
        if conn_type == 'smpp':
            try:
                data_obj = models.InsertSMPPAccount(**args.dict()) #convert into defined model, removing useless field
            except:
                resp_json = {
                    "errorcode":2,
                    "status": f"missing compulsory field"
                }
                return JSONResponse(status_code=500,content=resp_json)
        else:
            try:
                data_obj = models.InsertHTTPAccount(**args.dict()) #convert into defined model, removing useless field
            except:
                resp_json = {
                    "errorcode":2,
                    "status": f"missing compulsory field"
                }
                return JSONResponse(status_code=500,content=resp_json)
        name = data_obj.name.strip() #smpp_account.name should be unique
        ## remove any special char, replace space with _
        name = re.sub(r'\s',r'_', name) #abc xyz => abc_xyz
        existing_id = None
        cur.execute("select id from account where name=%s", (name,))
        try:
            existing_id = cur.fetchone()[0]
        except:
            pass

        if existing_id:
            resp_json = {
                "errorcode":2,
                "status": f"account name {name} exists"
            }
            return JSONResponse(status_code=403,content=resp_json)

        data_obj.name = name #put back cleaned name into object

    #### general processing for any table
    d_data = data_obj.dict()

    if table == 'account' and conn_type == 'smpp': # generate systemid/password/directory/notif3_dir
        name = d_data.get('name')
        ## create directory, notif_dir
        ext = generate_otp('lower',4) #give a random extension to avoid same subdir name, e.g abc4567
        systemid = name[:12]
        systemid = f"{re.sub(r'_$','',systemid)}_{ext}"
        subdir = systemid.upper()
        basedir = os.path.abspath(os.path.dirname(__file__))
        directory = os.path.join(basedir, f"sendxms/SERVER_SUPER100/received/{subdir}")
        notif3_dir = os.path.join(basedir, f"sendxms/SERVER_SUPER100/spool/{subdir}")
        d_data['directory'] = directory
        d_data['notif3_dir'] = notif3_dir 
        ## create systemid, password
        password = generate_otp('alphanumeric',8)

        d_data['systemid'] = systemid
        d_data['password'] = password
        logger.info(f"debug smpp_account: {json.dumps(d_data,indent=4)}")
    if table == 'account' and conn_type == 'http': # generate api_key/api_secret
        api_key = generate_otp('alphanumeric',20)
        api_secret = generate_otp('alphanumeric',40)
        d_data['api_key'] = api_key
        d_data['api_secret'] = api_secret

    data = dict() #hold the fields to be inserted into destination table
    
    fields,values = '', ''
    for k,v in d_data.items():
        if not v is None:
            data[k] = v
            fields += f"{k},"
            if isinstance(v, (int, float)): #is a number
                values += f"{v},"
            else:
                v = re.sub(r"'", "''",v) ##replace single quote ' with ''
                values += f"'{v}',"

    logger.debug(f"### after formatting and removing null value: {json.dumps(data,indent=4)}")

    fields = fields[:-1]
    values = values[:-1]

    sql = f"insert into {table} ({fields}) values ({values}) returning id;"
    logger.debug(sql)
    ### insert into table
    try:
        # new_id = cur.execute("""insert into billing_account (company_name,contact_name,billing_type,company_address,country,
        # city,postal_code,billing_email) values (%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        # (data['company_name'],data['contact_name'],data['billing_type'],data['company_address'],data['country'],data['city'],data['postal_code'],data['billing_email'])
        # )
        new_id = cur.execute(sql)
        try: 
            new_id = cur.fetchone()[0]
            if new_id:
                resp_json = {
                    "errorcode":0,
                    "status": "Success",
                    "id": new_id,
                    "result": data
                }
        except Exception as err:
            resp_json = {
                "errorcode":2,
                "status": f"insert {table} failure, no new id returned: {err}"
            }
            logger.info(f"reply internal insert: {json.dumps(resp_json,indent=4)}")
            return JSONResponse(status_code=500, content=resp_json)

    except Exception as err:
        resp_json = {
            "errorcode":2,
            "status": f"insert {table} failure: {err}"
        }
        logger.info(f"reply internal insert: {json.dumps(resp_json,indent=4)}")

        #raise HTTPException(status_code=500, detail={"errocode": 2, "status": f"insert DB error: {err}"})
        return JSONResponse(status_code=500, content=resp_json)      
    
    return JSONResponse(status_code=200,content=resp_json)



@app.post("/api/internal/update", 
#response_model=models.InsertResponse, 
            responses={404: {"errorcode": 1, "status": "some error msg"} }
)
async def update_record(
    args: models.InternalUpdate = Body(
                     ...,
                     examples=models.example_internal_update,
    ),
    #request: Request
):
    d_args = args.dict()
    logger.debug(f"### orig internal update request body: {json.dumps(d_args, indent=4)}")

    if not 'table' in d_args or not 'id' in d_args:
        resp_json = {
            "errorcode":2,
            "status": f"missing compulsory field table or id"
        }
        return JSONResponse(status_code=500,content=resp_json)

    table = d_args['table']
    id = d_args['id']

    if table == 'billing_account':
        try:
            data_obj = models.UpdateBillingAccount(**args.dict()) #convert into defined model, removing useless field
        except:
            resp_json = {
                "errorcode":2,
                "status": f"missing compulsory field"
            }
            return JSONResponse(status_code=500,content=resp_json)
        
        # if billing_email is provided, check if email is valid, comma seprated email
        if data_obj.billing_email: #email not null
            emails = data_obj.billing_email.split(',')
            for email in emails:
                try:
                    valid = validate_email(email) # return a email object
                except:
                    resp_json = {
                        "errorcode":1,
                        "status": f"Incorrect email address {email}"
                    }
                    return JSONResponse(status_code=422,content=resp_json)
                    break

    elif table == 'webuser': 
        try:
            data_obj = models.UpdateWebUser(**args.dict()) #convert into defined model, removing useless field
        except:
            resp_json = {
                "errorcode":2,
                "status": f"missing compulsory field"
            }
            return JSONResponse(status_code=500,content=resp_json)
        ### username and email should be unique
        username = data_obj.username
        email = data_obj.email
        existing_id_username = get_userid_from_username(username)
        existing_id_email = get_userid_from_username(email)
        if username and existing_id_username and existing_id_username != id:
            resp_json = {
                "errorcode":2,
                "status": f"username {username} exists"
            }
            return JSONResponse(status_code=403,content=resp_json)
        elif email and existing_id_email and existing_id_email != id:
            resp_json = {
                "errorcode":2,
                "status": f"email {email} exists"
            }
            return JSONResponse(status_code=403,content=resp_json)
    elif table == 'whitelist_ip':
        try:
            data_obj = models.UpdateWhitelistIP(**args.dict()) #convert into defined model, removing useless field
        except:
            resp_json = {
                "errorcode":2,
                "status": f"missing compulsory field"
            }
            return JSONResponse(status_code=500,content=resp_json)
    elif table == 'account':
        try:
            data_obj = models.UpdateAccount(**args.dict()) #convert into defined model, removing useless field
        except:
            resp_json = {
                "errorcode":2,
                "status": f"missing compulsory field"
            }
            return JSONResponse(status_code=500,content=resp_json)

    #### general processing for any table
    d_data = data_obj.dict()
    
    data = dict() #hold the fields to be updated to destination table
    
    set_cmd = ''
    for k,v in d_data.items():
        if not v is None:
            data[k] = v
            if isinstance(v, (int, float)): #is a number
                set_cmd += f"{k}={v},"
            else:
                v = re.sub(r"'", "''",v) ##replace single quote ' with ''
                set_cmd += f"{k}='{v}',"

    logger.debug(f"### after formatting and removing null: {json.dumps(data,indent=4)}")

    set_cmd = set_cmd[:-1] #remove ending ,

    sql = f"update {table} set {set_cmd},update_time=current_timestamp where id={id} returning id;"
    logger.debug(sql)
    ### insert into table
    try:
        new_id = cur.execute(sql)
        try: 
            new_id = cur.fetchone()[0]
            if new_id:
                resp_json = {
                    "errorcode":0,
                    "status": "Success",
                    "id": new_id,
                    "result": data
                }
                logger.debug(f"### reply internal update: {json.dumps(resp_json,indent=4)}")

        except Exception as err:
            resp_json = {
                "errorcode":2,
                "status": f"update {table} failed, no id returned: {err}"
            }
            logger.info(f"reply internal update: {json.dumps(resp_json,indent=4)}")
            return JSONResponse(status_code=500, content=resp_json)

    except Exception as err:
        resp_json = {
            "errorcode":2,
            "status": f"update {table} failed: {err}"
        }
        logger.info(f"reply internal update: {json.dumps(resp_json,indent=4)}")
        return JSONResponse(status_code=500, content=resp_json)      
    
    return JSONResponse(status_code=200,content=resp_json)

@app.post("/api/internal/password_hash")
async def get_password_hash(args: models.PasswordHashRequest):
    password_hash = generate_password_hash(args.password)
    resp_json = {
        "password": args.password,
        "password_hash": password_hash
    }
    return JSONResponse(content=resp_json)


@app.get("/api/internal/audit/{billing_id}", response_model=models.GetAuditResponse,
        responses={404: {"model": models.MsgNotFound}})
async def get_auditlog_by_billing_id(billing_id:int):
    cur.execute(f"""select a.creation_time,u.username,a.auditlog from audit a 
                join webuser u on a.webuser_id = u.id where u.billing_id={billing_id} order by a.creation_time desc limit 100;""")

    rows = cur.fetchall()
    l_data = list()
    for row in rows:
        (ts, username,auditlog) = row
        ts = ts.strftime("%Y-%m-%d, %H:%M:%S") #convert datetime.datetime obj to string
        d = {
            "timestamp": ts,
            "username": username,
            "audit": auditlog
        }
        l_data.append(d)
    
    resp_json = dict()

    if len(l_data) > 0:
        resp_json = {
            "errorcode":0,
            "status": "Success",
            "results": l_data
        }
    else:
        resp_json = {
            "errorcode": 1,
            "status":"Auditlog Not found!"
        }
        return JSONResponse(status_code=404, content=resp_json)
    
    return JSONResponse(status_code=200, content=resp_json)


@app.post("/api/internal/traffic_report")
async def traffic_report(
    args: models.TrafficReportRequest = Body(
        ...,
        examples = models.example_traffic_report_request,
    ),
):
    d_arg = args.dict()
    billing_id = d_arg.get("billing_id")
    start_date = d_arg.get("start_date",None)
    end_date = d_arg.get("end_date",None)
    if not start_date or not end_date: #default return past 7 days traffic
        sql = f"""select date(dbtime) as date, product.name as product, status,count(*) from cdr join product on product.id=cdr.product_id 
                where date(dbtime) >= current_date - interval '1 days' and billing_id={billing_id} group by date,product,status order by date;"""
    else:
        sql = f"""select date(dbtime) as date, product.name as product, status,count(*) from cdr join product on product.id=cdr.product_id  
        where dbtime between '{start_date}' and '{end_date}' and billing_id={billing_id} group by date,product,status order by date;"""
    logger.info(sql)
    l_data = list()
    data = defaultdict(dict)

    cur.execute(sql)
    rows = cur.fetchall()
    for row in rows:
        (day,product,status,qty) = row
        day = day.strftime("%Y-%m-%d")
        if not status or status == '':
            status = 'Pending'
        try:
            data[f"{day}---{product}"][status] += qty
        except:
            data[f"{day}---{product}"][status] = qty
    
    for key,d_value in sorted(data.items()):
        d = dict()
        total = 0
        for k,v in d_value.items():
            d[k] = v
            total += v
        day,product = key.split('---')
        d['date'] = day
        d['product'] = product
        d['total_sent'] = total
        l_data.append(d)
    
    if len(l_data) > 0:
        resp_json = {
            "errorcode" : 0,
            "status": "Success",
            "count": len(l_data),
            "results": l_data
        }
    else:
        resp_json = {
            "errorcode": 1,
            "status":"No Record found!"
        }
        return JSONResponse(status_code=404, content=resp_json)
    
    return JSONResponse(status_code=200, content=resp_json)
    
