from pydantic import BaseModel, EmailStr, Field
from typing import Optional,List
from datetime import datetime


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

class InternalSMS_BillingAccount(BaseModel):
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

    account: InternalSMS_BillingAccount
    cpg_id: Optional[int]

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
    description: str = Field(example="describe api_credential")


class AppResponse(BaseModel):
    errorcode: int=0
    status: str="Success"
    results: List[Application]

class GetWebUser(BaseModel):
    id: int
    username: str
    #password_hash: Optional[str]
    email: Optional[str]
    bnumber: Optional[str]
    role_id: Optional[int]
    role_name: Optional[str]
    live: Optional[int]

class GetUsersResponse(BaseModel):
    errorcode: int=0
    status: str="Success"
    results: List[GetWebUser]

class GetAudit(BaseModel):
    #timestamp: datetime #error: Object of type datetime is not JSON serializable
    timestamp: str
    username: str
    auditlog: str

class GetAuditResponse(BaseModel):
    errorcode: int=0
    status: str="Success"
    results: List[GetAudit]

class MsgNotFound(BaseModel):
    errorcode: int=1
    status: str = Field(default="Not found!")

class InternalInsert(BaseModel): #add all possible field here, depends on different table, some field may be null in request body
    table: str= Field(description="name of table")
    ### for billing_account
    billing_type: Optional[str] = Field(example='prepaid', description="postpaid or prepaid")
    company_name: Optional[str]
    company_address: Optional[str]
    contact_name: Optional[str]
    country: Optional[str]
    city: Optional[str]
    postal_code: Optional[str]
    billing_email: Optional[str]
    #billing_email: Optional[EmailStr] => need to handle custom validate error
    currency: Optional[str]
    ### for api_credential
    api_key: Optional[str]
    api_secret: Optional[str]
    product_id: Optional[int]
    callback_url: Optional[str]
    friendly_name: Optional[str]
    description: Optional[str]

    ### for webuser
    username: Optional[str]
    ## optional field
    password_hash: Optional[str]
    email: Optional[str]
    role_id: Optional[int]
    bnumber: Optional[str]
    ### for audit
    auditlog: Optional[str]
    ### common
    billing_id: Optional[int] #webuser, api_credential, audit
    webuser_id: Optional[int] #api_credential, audit

class InsertBillingAccount(BaseModel):
    ## compulsory field
    company_name: str
    contact_name: str
    ## optional field
    billing_type: Optional[str] = Field(example='prepaid', description="postpaid or prepaid", default='postpaid')
    company_address: Optional[str]
    country: Optional[str]
    city: Optional[str]
    postal_code: Optional[str]
    billing_email: Optional[str]
    #billing_email: Optional[EmailStr] => need to handle custom validate error
    
class InsertAPICredential(BaseModel):
    ## compulsory field
    api_key: str
    api_secret: str
    webuser_id: int
    product_id: int
    billing_id: int
    ## optional field
    callback_url: Optional[str]
    friendly_name: Optional[str]
    description: Optional[str]


class InsertWebUser(BaseModel):
    ## compulsory field
    username: str
    ## optional field
    password_hash: Optional[str]
    email: Optional[str]
    billing_id: Optional[int]
    role_id: Optional[int]
    bnumber: Optional[str]

class InsertAudit(BaseModel):
    ### for audit
    billing_id: int
    webuser_id: int
    auditlog: str

example_internal_insert={
    "billing_account": {
        "summary": "insert into billing_account",
        "value": {
            "table": "billing_account",
            "billing_type": "postpaid",
            "company_name": "ABC PTE LTD",
            "company_address": "Singapore",
            "contact_name":"Bob",
            "country":"Singapore",
            "city":"Singapore",
            "postal_code":"123456",
            "billing_email":"billing@abc.com",
            "currency":"USD"
        },
    },
    "api_credential": {
        "summary": "insert into api_credential",
        "value": {
            "table": "api_credential",
            "api_key": "abcdeft",
            "api_secret": "somegibberishtext",
            "webuser_id": 1001,
            "product_id": 0,
            "callback_url": "http://example.com/callback",
            "friendly_name": "Premium route for OTP",
            "billing_id": 1001,
            "description": "some text"
        },
    },
    "webuser": {
        "summary": "insert into webuser",
        "value": {
            "table": "webuser",
            "username": "Bob",
            "password_hash": "somegibberishtext",
            "email": "bob@example.com",
            "billing_id": 1001,
            "role_id": 3,
            "bnumber": "+6511223344"
        },
    },
    "audit": {
        "summary": "insert into audit",
        "value":{
            "table": "audit",
            "billing_id": 1001,
            "webuser_id": 1001,
            "auditlog": "access report"
        },
    }

}


class InternalUpdate(BaseModel): #add all possible field here, depends on different table, some field may be null in request body
    table: str= Field(description="name of table")
    id: int
    ### for billing_account
    company_name: Optional[str]
    contact_name: Optional[str]
    company_address: Optional[str]
    country: Optional[str]
    city: Optional[str]
    postal_code: Optional[str]
    billing_email: Optional[str]
    billing_type: Optional[str]
    currency: Optional[str]
    ### for api_credential
    api_key: Optional[str]
    api_secret: Optional[str]
    webuser_id: Optional[int]
    product_id: Optional[int]
    callback_url: Optional[str]
    friendly_name: Optional[str]
    description: Optional[str]
    ### for webuser
    username: Optional[str]
    password_hash: Optional[str]
    email: Optional[str]
    role_id: Optional[int]
    bnumber: Optional[str]
    ### common field
    billing_id: Optional[int] # in table webuser and api_credential
    deleted: Optional[int] # in table webuser and api_credential
    live: Optional[int] # in table api_credential, webuser
    
example_internal_update={
    "billing_account": {
        "summary": "update billing_account",
        "value": {
            "table": "billing_account",
            "id": 1,
            "billing_type": "postpaid",
            "company_name": "ABC PTE LTD",
            "company_address": "Singapore",
            "contact_name":"Bob",
            "country":"Singapore",
            "city":"Singapore",
            "postal_code":"123456",
            "billing_email":"billing@abc.com",
            "currency":"USD"
        },
    },
    "api_credential": {
        "summary": "update api_credential",
        "value": {
            "table": "api_credential",
            "id": 1,
            "api_key": "abcdeft",
            "api_secret": "somegibberishtext",
            "webuser_id": 1001,
            "product_id": 0,
            "callback_url": "http://example.com/callback",
            "friendly_name": "Premium route for OTP",
            "billing_id": 1001,
            "deleted": 0,
            "live": 0,
            "description": "some text"
        },
    },
    "webuser": {
        "summary": "update webuser",
        "value": {
            "table": "webuser",
            "id": 1,
            "username": "bob",
            "password_hash": "somegibberishtext",
            "email": "bob@example.com",
            "billing_id": 1001,
            "role_id": 3,
            "deleted": 0,
            "live": 0
        },
    },
}

class UpdateBillingAccount(BaseModel):
    company_name: Optional[str]
    contact_name: Optional[str]
    company_address: Optional[str]
    country: Optional[str]
    city: Optional[str]
    postal_code: Optional[str]
    billing_email: Optional[str]
    billing_type: Optional[str]
    currency: Optional[str]
    
class UpdateAPICredential(BaseModel):
    api_key: Optional[str]
    api_secret: Optional[str]
    webuser_id: Optional[int]
    product_id: Optional[int]
    billing_id: Optional[int]
    callback_url: Optional[str]
    friendly_name: Optional[str]
    deleted: Optional[int]
    live: Optional[int]
    description: Optional[str]


class UpdateWebUser(BaseModel):
    username: Optional[str]
    password_hash: Optional[str]
    email: Optional[str]
    billing_id: Optional[int]
    role_id: Optional[int]
    bnumber: Optional[str]
    deleted: Optional[int]
    live: Optional[int]


class PasswordHashRequest(BaseModel):
    password: str = Field(example="combination of letter,number and special characters")


example_internal_cpg={
    "valid_list_with_bnumber_only": {
        "summary": "in most cases uploaded list only contain bnumber",
        "value": {
            "blast_list": ["+6511223344\n","+6577889900"],
            "cpg_name": "promotion for black friday",
            "cpg_tpoa": "TopShop",
            "cpg_xms": "Enjoy 50% discount",
            "billing_id":1,
            "webuser_id":2,
            "product_id":0
        },
    },
    "valid_list_with_bnumber_and_variables": {
        "summary": "valid list with bnumber and variables",
        "value": {
            "blast_list": ["name,number\n","Bob,+6511223344\n","Alice,+6577889900\n"],
            "cpg_name": "promotion for black friday",
            "cpg_tpoa": "TopShop",
            "cpg_xms": "%name%, don't miss the sale, check promotion code send to %number%",
            "billing_id":1,
            "webuser_id":2,
            "product_id":0
        },
    },
}