from textwrap import indent
from typing import Optional,List
from fastapi import FastAPI, Body, Response, HTTPException, Depends
from pydantic import BaseModel, Field
from fastapi.security import HTTPBasic,HTTPBasicCredentials

import re
import smsutil
import random
from uuid import uuid4
import json

import myutils
from myutils import logger, config, read_config
import mysms
#from mysms import create_sms
from mydb import cur,r,g_account,g_numbering_plan
#import httpapi.myauth as myauth => does not work, saying there is no package httpapi
import myauth

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

class SMS(BaseModel): 
    #"from" is the public name of the field, can not use "from" direct as a field name in basemodel because it is keyword in python
    sender: str = Field(alias='from',description="SenderID", min_length=2, max_length=11, example="Example") 
    to: str = Field(description="receipient of the SMS, MSISDN, in E.164 format", 
                    min_length=10, max_length=20, example="96650403020")
    content: str = Field(description="SMS content. it can include any unicode defined characters in UTF-8 format",
                            example="Hello World!")
    base64url: Optional[int] = Field(default=0,description="to declare that content is base64url encoded. \
                                    this is recommended to avoid encoding issue for non-latin characters")
    status_report_req: Optional[int] = Field(alias='status-report-req', default=1) #can not use "-" directly in the base model

    udh: Optional[str] = Field(default="", description="for concatenated SMS, can specify udh here")

    # class Config:
    #     schema_extra = {
    #         "example": {
    #             "from": "NOC",
    #             "to": "971508020179",
    #             "content": "Hello Word"
    #         },
    #         "example": {
    #             "from": "NOC",
    #             "to": "971508020179",
    #             "content": "A man being mugged by two thugs put up a tremendous fight! Finally, the thugs subdued him and took his wallet. Upon finding only two dollars in the wallet, the surprised thug said \"Why did you put up such a fight?\" To which the man promptly replied \"I was afraid that you would find the $200 hidden in my shoe!\"",
    #         }
    #     }
class Msg(BaseModel):
    msgid: str = Field(description="unique message ID to identify an created SMS",example="77b16382-7871-40bd-a1ac-a26c6ccce687")
    to: str = Field(description="receipient of the SMS, MSISDN, in E.164 format", 
                    min_length=10, max_length=20, example="96650403020")

class SMSResponse(BaseModel):
    errorcode: int = Field(description="indicate result of creating SMS, 0 means successful", default=0)
    message_count: int = Field(alias="message-count",description="indicate the number of SMS created (for concatenated SMS)", default=1)
    messages: List[Msg]

class CallbackDLR(BaseModel):
    msisdn: str
    msgid: str
    status: str
    to: Optional[str]
    timestamp: Optional[str]

def is_empty(field):
    if field == '' or field == None:
        return True
    return False

@app.get('/')
async def home():
    return {'result': 'hello'}

#@app.post('/sms', status_code=201)
@app.post('/sms', response_model=SMSResponse, responses=mysms.example_create_sms_response)
#async def post_sms(response: Response,
async def post_sms(
                arg_sms: SMS = Body(
        ...,
        examples=mysms.example_create_sms,
    ),
#account: str = Depends(myauth.myauth_basic) 
account=Depends(myauth.authenticate) # multiple authentication methods, account is dict including many info

):
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
        udh_base = myutils.gen_udh_base()

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
            udh = myutils.gen_udh(udh_base,split,i+1)

        #errorcode = mysms.create_sms_file(account,sender,msisdn,xms,msgid,dcs,udh,require_dlr)
        
        data = {
            "msgid": msgid,
            "sender": sender,
            "to": msisdn,
            "content": xms,
            "require_dlr": require_dlr,
            "country_id": country_id,
            "operator_id": operator_id
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

@app.post('/dlr/callback_dlr', include_in_schema=False, status_code=200) # to receive push DLR from providers, don't expose in API docs
async def callback_dlr(arg_dlr: CallbackDLR):
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






    
