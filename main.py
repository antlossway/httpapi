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
import requests

#import myutils
from myutils import logger, read_comma_sep_lines, gen_udh_base, gen_udh, generate_otp
import mysms
#from mysms import create_sms
from mydb import cur,r,g_numbering_plan
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

desc = """
**Our REST API is using OpenAPI standard. It allows you to send SMS and query delivery status.**\n
Delivery Report can also be pushed to client’s WebHook (HTTP(S) callback).\n
Mobile Numbers are specified in E.164 format (International format including country code).\n
HTTP Request and Response Body use JSON format.\n
Each request requires a Basic Authentication (api_key/api_secret) with IP whitelisting.

## Create SMS (production)
Require Basic Authentication + IP filtering\n
support
 * Single short SMS
 * Single long SMS (concatenated, multi-part)
 * Bulk SMS (comma separated MSISDN list)

**POST https://dev1.ameex-mobile.com/api/sms**

## Create SMS (Test only)
This is to help you test request/response format, no SMS is really created or charged.\n
Require Basic Authentication, no IP filtering

**POST https://dev1.ameex-mobile.com/api/test/sms**

## Query Status (production)
Require Basic Authentication + IP filtering

**GET https://dev1.ameex-mobile.com/api/sms/{msgid}**

## Query Status (Test only)
Require Basic Authentication, no IP filtering

**GET https://dev1.ameex-mobile.com/api/test/sms/{msgid}**


## Test Delivery Report(DR) Callback (Test only)
The Customer need to deploy an HTTP(S) server to receive DR receipt pushed from our CloudSMS Platform.\n
This endpoint is to help you to test the format of push DR that is posted to the callback_url you provided.

In production, DR will only be pushed when there is DR returned by the landing operator.

**POST https://dev1.ameex-mobile.com/api/test/callback_dlr**

"""

tags_metadata = [
    {
        "name": "Test Create SMS",
        "description": "This is to help you test request/response, no SMS is really created or charged, Require Basic Authentication, no IP filtering",
    },
    {
        "name": "Test Query SMS Status",
        "description": "This is to help develper test request/response. Require Basic Authentication, no IP filtering",
    },

    {
        "name": "Test callback: push DLR format",
        "description": "Client provide callback_url to test the push DLR format",
    },

    {
        "name": "Create SMS",
        "description": "production endpoint to send SMS, Require Basic Authentication + IP filtering",
        #"externalDocs": {
        #    "description": "Items external docs",
        #    "url": "https://fastapi.tiangolo.com/",
        #},
    },
    {
        "name": "Query SMS Status",
        "description": "Client can query individule SMS's delivery status by providing msgid, Require Basic Authentication + IP filtering",
    },

]

app = FastAPI(
    title="CMI CloudSMS API",
    description=desc,
    openapi_tags=tags_metadata,
    version="0.1.0",
    #terms_of_service="http://example.com/terms/",
    docs_url='/api/docs', 
    redoc_url='/api/redoc',
    openapi_url='/api/openapi.json'

)

    
def is_empty(field):
    if field == '' or field == None:
        return True
    return False

#@app.get('/')
#async def home():
#    return {'result': 'hello'}

@app.post('/api/sms', response_model=models.SMSResponse, responses=mysms.example_create_sms_response, tags=["Create SMS"])
#async def post_sms(response: Response,
async def create_sms(request: Request,
                arg_sms: models.SMS = Body(
                    ...,
                    examples=mysms.example_create_sms,
                ),
account=Depends(myauth.authenticate) # multiple authentication methods, account is dict including many info
):
    result = post_sms(request,account,arg_sms,"prod")
    return result
 
@app.post('/api/test/sms', response_model=models.SMSResponse, responses=mysms.example_create_sms_response, tags=["Test Create SMS"])
async def test_create_sms(request: Request,
                arg_sms: models.SMS = Body(
                    ...,
                    examples=mysms.example_create_sms,
                ),
account=Depends(myauth.authenticate_test)
):
    result = post_sms(request,account,arg_sms,"test")
    return result
 
def post_sms(request: Request,account,arg_sms,mode):
    logger.info(request.headers) #debug raw request
    d_sms = arg_sms.dict()

    logger.info(f"debug post body")
    for k,v in d_sms.items():
        logger.info(f"{k}: {v}({type(v)})")
    
    sender = d_sms.get("sender", None) #client may sent "from", which is alias as "sender"
    l_bnumber_in = d_sms.get("to", None).split(',') # single bnumber or comma separated bnumber (for bulk)
    content = d_sms.get("content", None)

    l_bnumber = list() #to keep the final cleaned bnumber or bnumber list
    for bnumber in l_bnumber_in:
        bnumber = mysms.clean_msisdn(bnumber)
        if bnumber:
            l_bnumber.append(bnumber)

    if len(l_bnumber) == 0:
        resp_json = {
            "errorcode": 1003,
            "errormsg": f"No valid B-number found"
        }
        logger.info("### post_sms reply client:")
        logger.info(json.dumps(resp_json, indent=4))
 
        return JSONResponse(status_code=422, content=resp_json)
    
    result = {}

    ### missing parameters
    if is_empty(sender) or is_empty(content):
        resp_json = {
            "errorcode": 1002,
            "errormsg": "missing parameter, please check if you forget 'from' or 'content'"
         }
        logger.info("### post_sms reply client:")
        logger.info(json.dumps(resp_json, indent=4))
 
        return JSONResponse(status_code=422, content=resp_json)

    ### sender format wrong
    len_sender = len(sender)
    if len_sender > max_len_tpoa:
        resp_json = {
            "errorcode": 1004,
            "errormsg": f"TPOA/Sender length should not be more than {max_len_tpoa} characters"
        }
        logger.info("### post_sms reply client:")
        logger.info(json.dumps(resp_json, indent=4))
 
        return JSONResponse(status_code=422, content=resp_json)

    ### optional param
    base64 = arg_sms.base64url #default 0, non-zero is considered 1
    require_dlr = arg_sms.status_report_req #default 1
    orig_udh = arg_sms.udh #default None

    if base64 != 0:
        logger.info("##### incoming content is already base64url encoded, qrouter should decoded it")

    ### get split info
    sms = smsutil.split(content)
    split = len(sms.parts)
    encoding = sms.encoding

    logger.info(f"SMS content has {split} part, encoding {encoding}")
    dcs = 0
    if not encoding.startswith('gsm'): #gsm0338 or utf_16_be
        dcs = 8 
    
    udh_base = ''
    udh = ''

    if split > 1:
        udh_base = gen_udh_base()
        logger.debug(f"gen_udh_base: {udh_base}")

    l_resp_msg = list() #list of dict

    for bnumber in l_bnumber:
        ### disable parse_bnumber as it's adding computing load and our current API does not return country/operator info to client ###
        """
        check B-number country/operator
        """
        #parse_result = mysms.parse_bnumber(g_numbering_plan,bnumber)
        #if parse_result:
        #    country_id,operator_id = parse_result.split('---')

        for i,part in enumerate(sms.parts):
            xms = part.content
            msgid = str(uuid4())
    
            resp_msg = {"msgid": msgid, "to": bnumber, "encoding": encoding}
            l_resp_msg.append(resp_msg)
    
            if orig_udh != None and orig_udh != '':
                udh = orig_udh
                logger.info(f"keep orig UDH {udh}")
            
            #for long sms, our UDH will override orig UDH from client
            if udh_base != '':
                udh = gen_udh(udh_base,split,i+1)
                logger.debug(f"gen_udh: {udh}")
            
            data = {
                "msgid": msgid,
                "sender": sender,
                "to": bnumber,
                "content": xms,
                "udh": udh,
                "dcs": dcs,
                "base64url": base64
                #"country_id": country_id,  ## qrouter will take care parse_bnumber for both smpp and http(again)
                #"operator_id": operator_id,
            }
            
            if require_dlr == 0: #by default require_dlr=1,so no need to add
                data["require_dlr"] = 0
            
    #        account = {
    #        "api_key": api_key,
    #        }
    
            if mode == "test":
                errorcode = 0
                logger.info("Test only, don't create SMS")
            else:
                errorcode = mysms.create_sms(account,data)
    
            if errorcode == 0:
                pass
            else: #no need to process remain parts
                resp_json = {
                    "errorcode": 6,
                    "errormsg": "Internal Server Error, please contact support"
                }
                logger.info("### post_sms reply client:")
                logger.info(json.dumps(resp_json, indent=4))
 
                return JSONResponse(status_code=500, content=resp_json)
    
                break

    resp_json = {
                 'errorcode': errorcode,
                 'message-count': len(l_resp_msg),
                 'messages': l_resp_msg
                }
    logger.info("### reply client:")
    logger.info(json.dumps(resp_json, indent=4))
 
    #return resp_json
    return JSONResponse(status_code=200, content=resp_json)


#@app.post('/api/callback_dlr', include_in_schema=False, status_code=200) # to receive push DLR from providers, don't expose in API docs
@app.post('/api/test/callback_dlr', status_code=200, response_model=models.TestCallbackResponse, tags=["Test callback: push DLR format"])
async def callback_dlr(arg_d: models.TestCallbackRequest):
    d = arg_d.dict()
    logger.info(json.dumps(d, indent=4))
    callback_url = arg_d.callback_url

    req_json = {
        "msisdn": "6588001000",
        "sender": "INFO",
        "msgid": "77b16382-7871-40bd-a1ac-a26c6ccce687",
        "status": "DELIVERD",
        "timestamp": "2022-02-01 00:00"
    }

    logger.info(f"### post test DLR to client's callback url {callback_url}:\n{json.dumps(req_json, indent=4)}")
    try:
        resp = requests.post(callback_url, json=req_json, timeout=(1,3))
    except:
        logger.info(f"!!! can not connect {callback_url}")

    try:
#        req_body = resp.request.body.decode() # resp.request.body is byte, use decode() to turn to str
#        req_json = json.loads(req_body) # convert from str to json
#        logger.info(f"### post test DLR to client's callback url {callback_url}:\n{json.dumps(req_json, indent=4)}")

        resp_json = resp.json()
        print(f"response from {callback_url}: {json.dumps(resp_json, indent=4)}")
    except:
        print(f"!!! no response from {callback_url}")
        pass

#    logger.info("### print example push DLR format to client:")
#    logger.info(json.dumps(req_json, indent=4))
 
    #return resp_json
    return JSONResponse(status_code=200, content=req_json)

#### query SMS status production
@app.get('/api/sms/{msgid}', response_model=models.QueryStatusResponse, 
         responses={404: {"model": models.MsgNotFound}},
         tags=["Query SMS Status"])
async def query_sms_status(msgid: str, account=Depends(myauth.authenticate) # multiple authentication methods, account is dict including many info
):
    ### check if redis has cache
    index = f"STATUS:{msgid}"
    res = r.hgetall(index)
    d_res = { k.decode('utf-8'): res.get(k).decode('utf-8') for k in res.keys() }


    ### either Pending or redis cache expired, query postgreSQL
    if len(d_res) == 0:
        logger.info(f"redis {index} does not exist, maybe still pending or redis expire, check cdr table")
        cur.execute("select tpoa,bnumber,status,notif3_dbtime from cdr where msgid=%s", (msgid,))
        row = cur.fetchone()
        try:
            (tpoa,bnumber,status,notif3_dbtime) = row
    
            if not status or status == '':
                status = 'Pending'
                timestamp = ""
            else:
                timestamp = notif3_dbtime.strftime("%Y-%m-%d, %H:%M:%S")
    
            resp_json = {
                "msisdn": bnumber,
                "sender": tpoa,
                "msgid": msgid,
                "status": status,
                "timestamp": timestamp
            }
            logger.info(f"query_sms_status found status in cdr, result: {json.dumps(resp_json,indent=4)}")

        except: # no record found in postgreSQL cdr table
            resp_json = {
                "errorcode": 1006,
                "errormsg": "MsgID not found"
            }
            return JSONResponse(status_code=404, content=resp_json)

    else:
        logger.info(f"query_sms_status found entry in redis {index}, result: {json.dumps(d_res,indent=4)}")
        resp_json = d_res
    
    return JSONResponse(status_code=200, content=resp_json)

#### query SMS status test
@app.get('/api/test/sms/{msgid}', response_model=models.QueryStatusResponse, 
         responses={404: {"model": models.MsgNotFound}},
         tags=["Test Query SMS Status"])
async def test_query_sms_status(msgid: str, account=Depends(myauth.authenticate_test) # multiple authentication methods, account is dict including many info
):
    ### check if redis has cache
    index = f"STATUS:{msgid}"
    res = r.hgetall(index)
    d_res = { k.decode('utf-8'): res.get(k).decode('utf-8') for k in res.keys() }


    ### either Pending or redis cache expired, query postgreSQL
    if len(d_res) == 0:
        logger.info(f"redis {index} does not exist, maybe still pending or redis expire, check cdr table")
        cur.execute("select tpoa,bnumber,status,notif3_dbtime from cdr where msgid=%s", (msgid,))
        row = cur.fetchone()
        try:
            (tpoa,bnumber,status,notif3_dbtime) = row
    
            if not status or status == '':
                status = 'Pending'
                timestamp = ""
            else:
                timestamp = notif3_dbtime.strftime("%Y-%m-%d, %H:%M:%S")
    
            resp_json = {
                "msisdn": bnumber,
                "sender": tpoa,
                "msgid": msgid,
                "status": status,
                "timestamp": timestamp
            }
            logger.info(f"test_query_sms_status found status in cdr, result: {json.dumps(resp_json,indent=4)}")

        except: # no record found in postgreSQL cdr table
            resp_json = {
                "errorcode": 1006,
                "errormsg": "MsgID not found"
            }
            return JSONResponse(status_code=404, content=resp_json)

    else:
        logger.info(f"test_query_sms_status found entry in redis {index}, result: {json.dumps(d_res,indent=4)}")
        resp_json = d_res
    
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


