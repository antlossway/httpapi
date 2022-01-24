"""
https://stackoverflow.com/questions/64731890/fastapi-supporting-multiple-authentication-dependencies
"""
from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBasic,HTTPBasicCredentials
import secrets
import hashlib

from myutils import logger
from mydb import g_userinfo, g_account

security = HTTPBasic()

## test only 
users = {
    "bob": {
        "secret": "pass",
        "name": "Bob",
        "dir": "/tmp/sms/BOB/"
    },
    "alice":{
        "secret": "pass",
        "name": "Alice",
        "dir": "/tmp/sms/ALICE/"
    }
}

def calculate_md5_hex(input_str:str):
    str2bytes = input_str.encode() #convert string into bytes to feed hash function
    md5_hex = hashlib.md5(str2bytes) #encode the bytes
    return md5_hex.hexdigest()

#### legacy code logic of A2P server
def myauth_basic_legacy(credentials: HTTPBasicCredentials = Depends(security)):
    logger.debug(f"debug: {credentials}")
    #ac = users.get(credentials.username,None)
    #expected_secret_enc = ac.get('secret')

    ac = g_userinfo.get(credentials.username,None) #dict

    if ac:
        expected_secret_enc = ac.get('secret_enc',None)
        acname = ac.get('name')
        salt = ac.get('salt')
    
        if expected_secret_enc:
            received_secret_enc = calculate_md5_hex(credentials.password + salt)
            logger.debug(f"debug: account found with api_key {credentials.username}, expected_secret_enc: {expected_secret_enc} \
                    received_secret_enc: {received_secret_enc}")
            password_match = secrets.compare_digest(received_secret_enc, expected_secret_enc )
            #password_match = True
            if password_match:
                return acname
    logger.warning("debug: basic auth failed")

    #either api_key or api_secret does not match
    # raise HTTPException(
    #     status_code=401, #unauthorized
    #     detail="Incorrect api_key or api_secret"
    # )
    return False


### new basic auth, get more info for account 
def myauth_basic(credentials: HTTPBasicCredentials = Depends(security)):
    logger.debug(f"debug: {credentials}")

    ac = g_account.get(credentials.username,None) #dict
    #     ac = {
    #     "api_key": api_key,
    #     "api_secret": api_secret,
    #     "billing_id": billing_id,
    #     "company_name": company_name,
    #     "webuser_id": webuser_id,
    #     "webuser_name": webuser_name,
    #     "product_id": product_id,
    #     "product_name": product_name
    #     }

    if ac:
        expected_secret = ac.get('api_secret',None)
    
        if expected_secret:
            logger.debug(f"debug: account found with api_key {credentials.username}, expected_secret: {expected_secret} \
                    received_secret: {credentials.password}")
            password_match = secrets.compare_digest(credentials.password, expected_secret )
            if password_match:
                return ac
    logger.warning("debug: basic auth failed")

    #either api_key or api_secret does not match
    # raise HTTPException(
    #     status_code=401, #unauthorized
    #     detail="Incorrect api_key or api_secret"
    # )
    return False

def myauth_jwt():
    return False

async def authenticate(basic_result=Depends(myauth_basic), jwt_result=Depends(myauth_jwt)):
    if not (basic_result or jwt_result):
        logger.debug("debug: none of the auth method pass")

        raise HTTPException(
            status_code=401, #unauthorized
            detail="Incorrect api_key or api_secret"
        )
    else:
        logger.debug("debug: one of the auth method pass")
        if basic_result:
            return basic_result
        else:
            return jwt_result

whitelist_ip = ['127.0.0.1','localhost','13.214.145.167']
async def allowinternal(request: Request):
    client_ip = request.client.host
    if not request.client.host in whitelist_ip:
        raise HTTPException(status_code=401, detail=f"unauthorized access")
