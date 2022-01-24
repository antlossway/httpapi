from pydantic import BaseModel, Field
from typing import Optional,List


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


class InternalNewCampaign(BaseModel):
    blast_list: List[str]
    cpg_name: str
    cpg_tpoa: str
    cpg_xms: str
    billing_id: int
    webuser_id: int
    product_id: int

class BillingAccount(BaseModel):
    billing_id: int
    webuser_id: int
    product_id: int

class InternalSMS(BaseModel):
    sender: str = Field(alias='from',description="SenderID", min_length=2, max_length=11, example="Example") 
    to: str = Field(description="receipient of the SMS, MSISDN, in E.164 format", 
                    min_length=10, max_length=20, example="96650403020")
    content: str = Field(description="SMS content. it can include any unicode defined characters in UTF-8 format",
                            example="Hello World!")
    udh: Optional[str] = Field(default="", description="for concatenated SMS, can specify udh here")

    account: BillingAccount

class InternalLogin(BaseModel):
    username: str = Field(description="username",example="admin")
    password: str = Field(description="password", example="abcd")

class Application(BaseModel):
    id: int
    friendly_name: str = Field(example="premium route for OTP")
    api_key: str = Field(example="abcdefghijk")
    api_secret: str = Field(example="32y41h41ojhhriugr3gr")
    callback_url: str = Field(example="http://example.com/callback")
    live: int = Field(description="indicate result validity of application, 1:valid, 0:not valid", default=1)
    product_id: int = Field(example=1)
    product: str = Field(description="name of product", example="Premium SMS")
    webuser_id: int = Field(example=1)

class AppResponse(BaseModel):
    errorcode: int=0
    status: str="Success"
    results: List[Application]

class MsgNotFound(BaseModel):
    errorcode: int=1
    status: str = Field(default="Not found!")