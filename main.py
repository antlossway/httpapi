#from textwrap import indent
#from typing import Optional,List
#from pydantic import BaseModel, Field

from subprocess import call
from fastapi import FastAPI, Body, Response, HTTPException, Depends, Request
from fastapi.responses import JSONResponse

import re
import smsutil
import random
from uuid import uuid4
import json

#import myutils
from myutils import logger, read_comma_sep_lines, gen_udh_base, gen_udh
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
        # result = {
        #     "errorcode": 2,
        #     "errormsg": "missing parameter, please check if you forget 'from','to',or 'content'"
        # }
        raise HTTPException(status_code=422, detail=f"missing parameter, please check if you forget 'from','to',or 'content'")

    ### msisdn format wrong
    msisdn = mysms.clean_msisdn(msisdn)
    if not msisdn:
        raise HTTPException(status_code=422, detail=f"B-number {msisdn} is invalid")
    
    ### sender format wrong
    len_sender = len(sender)
    if len_sender > max_len_tpoa:
        # result = {
        #     "errorcode": 4,
        #     "errormsg": f"TPOA/Sender length should not be more than {max_len_tpoa} characters"
        # }
        # response.status_code = 422

        raise HTTPException(status_code=422, detail=f"TPOA/Sender length should not be more than {max_len_tpoa} characters")

    ### check B-number country/operator ###
    parse_result = mysms.parse_bnumber(g_numbering_plan,msisdn)
    if parse_result:
        country_id,operator_id = parse_result.split('---')
    else:
        raise HTTPException(status_code=422, detail=f"Receipient number does not belong to any network")

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
        print(f"debug udh_base {udh_base}")

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
            print(f"debug udh: {udh}")

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
            raise HTTPException(status_code=500, detail=f"Internal Server Error, please contact support")
            break

    resp_json = {
                 'errorcode': errorcode,
                 'message-count': split,
                 'messages': l_resp_msg
                }
    logger.info("### reply client:")
    logger.info(json.dumps(resp_json, indent=4))
 
    return resp_json

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
async def create_campaign(arg_new_cpg: models.InternalNewCampaign, request: Request):

    # blast_list: List[str]
    # cpg_name: str
    # cpg_tpoa: str
    # cpg_xms: str
    # billing_id: int
    # webuser_id: int
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
        webuser_id = arg_new_cpg.webuser_id
        product_id = arg_new_cpg.product_id
        sql = f"""insert into cpg (name,tpoa,billing_id,webuser_id,product_id,xms) values 
                ('{cpg_name}','{tpoa}',{billing_id},{webuser_id},{product_id},'{xms}') returning id;"""
        print(sql)
        cur.execute(sql)
        cpg_id = cur.fetchone()[0]

        for d in l_data:
            hash_value = d.get('hash',None)
            if hash_value:
                del d['hash'] #delete 'hash' from the dict
                for k,v in d.items():
                    sql = f"""insert into cpg_blast_list (cpg_id,field_name,value,hash) values ({cpg_id}, '{k}','{v}','{hash_value}');"""
                    print(sql)
                    try:
                        cur.execute(sql)
                    except Exception as err:
                        print(f"!!! insertion error {err}")
        resp_json = {
            'cpg_id': cpg_id,
            'count_valid_entry': len(l_data)
        }

    logger.info("### reply UI:")
    logger.info(json.dumps(resp_json, indent=4))
 
    return resp_json

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

    result = {}

    ### missing parameters
    if is_empty(sender) or is_empty(msisdn) or is_empty(content):
        # result = {
        #     "errorcode": 2,
        #     "errormsg": "missing parameter, please check if you forget 'from','to',or 'content'"
        # }
        raise HTTPException(status_code=422, detail=f"missing parameter, please check if you forget 'from','to',or 'content'")

    ### msisdn format wrong
    msisdn = mysms.clean_msisdn(msisdn)
    if not msisdn:
        raise HTTPException(status_code=422, detail=f"B-number {msisdn} is invalid")
    
    ### sender format wrong
    len_sender = len(sender)
    if len_sender > max_len_tpoa:
        # result = {
        #     "errorcode": 4,
        #     "errormsg": f"TPOA/Sender length should not be more than {max_len_tpoa} characters"
        # }
        # response.status_code = 422

        raise HTTPException(status_code=422, detail=f"TPOA/Sender length should not be more than {max_len_tpoa} characters")

    ### check B-number country/operator ###
    parse_result = mysms.parse_bnumber(g_numbering_plan,msisdn)
    if parse_result:
        country_id,operator_id = parse_result.split('---')
    else:
        raise HTTPException(status_code=422, detail=f"Receipient number does not belong to any network")

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
        print(f"debug udh_base {udh_base}")

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
            print(f"debug udh: {udh}")

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
        
        account = arg_sms.account.dict()

        errorcode = mysms.create_sms(account,data,'AMEEX_PREMIUM')

        if errorcode == 0:
            pass
        else: #no need to process remain parts
            raise HTTPException(status_code=500, detail=f"Internal Server Error, please contact support")
            break

    resp_json = {
                 'errorcode': errorcode,
                 'message-count': split,
                 'messages': l_resp_msg
                }
    logger.info("### reply client:")
    logger.info(json.dumps(resp_json, indent=4))
 
    return resp_json

from werkzeug.security import generate_password_hash,check_password_hash

@app.post('/api/internal/login') #check webuser where deleted=0
async def verify_login(arg_login: models.InternalLogin, request:Request, response:Response):
#async def verify_login(arg_login: models.InternalLogin, request:Request, response:Response, auth_result=Depends(myauth.allowinternal)):
    # check if username exists
    cur.execute("""select u.id as webuser_id,username,password_hash,email,bnumber,role_id,webrole.name as role_name,
    billing_id,b.billing_type,b.company_name,b.company_address,b.country,b.city,b.postal_code,b.currency from webuser u
        join billing_account b on u.billing_id=b.id join webrole on u.role_id=webrole.id where username=%s and deleted=0
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

    return resp_json

# use responses to add additional response like returning errors
@app.get("/api/internal/application/{billing_id}", response_model=models.AppResponse,
        responses = {404: {"model": models.MsgNotFound}}
) #get all api_credentials for a billing account
def get_app(billing_id: int, response:Response):
    cur.execute(f"""select a.id, api_key,api_secret,webuser_id,product_id,product.name as product_name,a.live,callback_url,
    friendly_name from api_credential a join product on product.id=a.product_id where a.billing_id=%s and a.deleted=0;
    """, (billing_id,))

    l_data = list() #list of dict
    rows = cur.fetchall()
    for row in rows:
        (api_id,api_key,api_secret,webuser_id,product_id,product_name,live,callback_url,friendly_name) = row
        d = {
            "id": api_id,
            "friendly_name": friendly_name,
            "api_key": api_key,
            "api_secret": api_secret,
            "callback_url": callback_url,
            "live": live,
            "product_id": product_id,
            "product": product_name,
            "webuser_id": webuser_id
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
            "status":"App Not found!"
        }
        return JSONResponse(status_code=404, content=resp_json)
    
    return resp_json