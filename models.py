from pydantic import BaseModel, EmailStr, Field
from typing import Optional,List
from datetime import datetime


class SMS(BaseModel): 
    #"from" is the public name of the field, can not use "from" direct as a field name in basemodel because it is keyword in python
    sender: str = Field(alias='from',description="SenderID", min_length=2, max_length=11, example="Example") 
    to: str = Field(description="receipient of the SMS, MSISDN, in E.164 format, multiple numbers should be separated by comma", 
                    example="6588001000")
    content: str = Field(description="SMS content. it can include any unicode defined characters in UTF-8 format",
                            example="Hello World!")
    base64url: Optional[int] = Field(default=0,description="to declare that content is base64url encoded. \
                                    this is recommended to avoid encoding issue for non-latin characters")
    status_report_req: Optional[int] = Field(alias='status-report-req', default=1) #can not use "-" directly in the base model

    udh: Optional[str] = Field(default="", description="for concatenated SMS, can specify udh here")

    class Config:
        schema_extra = {
            "example": {
                "sender": "Foo",
                "to": "6588001000",
                "content": "Have a nice day!"
        }
    }

class Msg(BaseModel):
    msgid: str = Field(description="unique message ID to identify an created SMS",example="77b16382-7871-40bd-a1ac-a26c6ccce687")
    to: str = Field(description="receipient of the SMS, MSISDN, in E.164 format", 
                    min_length=10, max_length=20, example="96650403020")
    encoding: str = Field(description="encoding", example="gsm0338, utf_16_be")

class SMSResponse(BaseModel):
    errorcode: int = Field(description="indicate result of creating SMS, 0 means successful", default=0)
    message_count: int = Field(alias="message-count",description="indicate the number of SMS created (for concatenated SMS or bulk SMS)", default=1)
    messages: List[Msg]


### reply query status from our HTTP client
class QueryStatusResponse(BaseModel):
    msisdn: str = Field(example='6588001000')
    sender: Optional[str] = Field(example='INFO')
    msgid: str = Field(example="77b16382-7871-40bd-a1ac-a26c6ccce687")
    status: str = Field(example="DELIVERD")
    #dlr_timestamp: Optional[str] = Field(alias="dlr-timestamp", example="2022-01-29 00:00")
    timestamp: Optional[str] = Field(example="2022-02-01 00:00")


### developer can test what will be received on callback_url for DLR status
class TestCallbackRequest(BaseModel):
    callback_url: str = Field(example="https://example.com/callback")
    
class TestCallbackResponse(BaseModel):
    msisdn: str = Field(example='6588001000')
    sender: Optional[str] = Field(example='INFO')
    msgid: str = Field(example="77b16382-7871-40bd-a1ac-a26c6ccce687")
    status: str = Field(example="DELIVERD")
    #dlr_timestamp: Optional[str] = Field(alias="dlr-timestamp", example="2022-01-29 00:00")
    timestamp: Optional[str] = Field(example="2022-02-01 00:00")

class MsgNotFound(BaseModel):
    errorcode: int=1
    errormsg: str = Field(default="Not found!")


