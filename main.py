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
import mydb
#import httpapi.myauth as myauth => does not work, saying there is no package httpapi
import myauth

########################
### Global Variables ###
########################

min_len_msisdn = 10 #without prefix like 00 or +
max_len_msisdn = 15 #ITU-T recommendation E.164
max_len_tpoa = 11

app = FastAPI()

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
    msisdn = re.sub(r'^\++', r'', msisdn)
    msisdn = re.sub(r'^0+', r'', msisdn)
    len_to = len(msisdn)
    if len_to < min_len_msisdn or len_to > max_len_msisdn or re.match(r'\D', msisdn):
        # result = {
        #     "errorcode": 3,
        #     "errormsg": "B-number is invalid"
        # }
        # response.status_code = 422
        raise HTTPException(status_code=422, detail=f"B-number {msisdn} is invalid")


    msisdn = f"+{msisdn}" #put back + at beginning
    
    ### sender format wrong
    len_sender = len(sender)
    if len_sender > max_len_tpoa:
        # result = {
        #     "errorcode": 4,
        #     "errormsg": f"TPOA/Sender length should not be more than {max_len_tpoa} characters"
        # }
        # response.status_code = 422

        raise HTTPException(status_code=422, detail=f"TPOA/Sender length should not be more than {max_len_tpoa} characters")

    ### TBD check B-number country/operator ###

    ### TBD chck 

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
            "country_id": 3,
            "operator_id": 3
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
