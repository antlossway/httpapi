"""
https://stackoverflow.com/questions/64731890/fastapi-supporting-multiple-authentication-dependencies
"""
from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBasic,HTTPBasicCredentials
import secrets
import hashlib
from collections import defaultdict
import json
import os

from myutils import logger
from mydb import g_account

security = HTTPBasic()

basedir = os.path.abspath(os.path.dirname(__file__))
auth_file = os.path.join(basedir, ".htaccess")
whitelist_ip = ['127.0.0.1','localhost','13.214.145.167','95.216.217.218'] # 13.214.145.167 => frontend, 95.216.217.218 => h-dev on hetzner

def calculate_md5_hex(input_str:str):
    str2bytes = input_str.encode() #convert string into bytes to feed hash function
    md5_hex = hashlib.md5(str2bytes) #encode the bytes
    return md5_hex.hexdigest()

#### legacy code logic of A2P server
#def myauth_basic_legacy(credentials: HTTPBasicCredentials = Depends(security)):
#    logger.debug(f"debug: {credentials}")
#
#    ac = g_userinfo.get(credentials.username,None) #dict
#
#    if ac:
#        expected_secret_enc = ac.get('secret_enc',None)
#        acname = ac.get('name')
#        salt = ac.get('salt')
#    
#        if expected_secret_enc:
#            received_secret_enc = calculate_md5_hex(credentials.password + salt)
#            logger.debug(f"debug: account found with api_key {credentials.username}, expected_secret_enc: {expected_secret_enc} \
#                    received_secret_enc: {received_secret_enc}")
#            password_match = secrets.compare_digest(received_secret_enc, expected_secret_enc )
#            #password_match = True
#            if password_match:
#                return acname
#    logger.warning("debug: basic auth failed")
#
#    return False


### new basic auth, get more info for account , but g_account is only loaded when server started, now we use myauth_basic_authfile instead
def myauth_basic(credentials: HTTPBasicCredentials = Depends(security)):
    logger.debug(f"debug: {credentials}")

    ac = g_account.get(credentials.username,None) #dict
#        ac = {
#        "api_key": api_key,
#        "api_secret": api_secret,
#        "account_id": account_id,
#        "billing_id": billing_id,
#        "company_name": company_name,
#        "product_id": product_id,
#        "product_name": product_name,
#        "callback_url": callback_url
#        }

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

def read_auth():
    d_secret = dict()
    d_ips = defaultdict(list)

    with open(auth_file, 'r', encoding='utf-8') as r:
        for line in r:
            api_key,api_secret,ips = line.strip().split("---")
            d_secret[api_key] = api_secret
            d_ips[api_key] = ips.split(",")
    #print(json.dumps(d_secret, indent=4), json.dumps(d_ips, indent=4))

    return d_secret,d_ips 

### new basic auth, check ~/httpapi/.htaccess, which is generated by cronjob every min, new account will be reflected with max 1 min delay
def myauth_basic_authfile(request:Request, credentials: HTTPBasicCredentials = Depends(security)):
    orig_ip = request.client.host
    api_key = credentials.username

    d_secret,d_ips = read_auth()
    logger.debug(f"debug: client IP: {orig_ip}, d_secret: {json.dumps(d_secret, indent=4)}, d_ips: {json.dumps(d_ips, indent=4)}")

    if not api_key in d_secret:
        logger.warning("debug: no account found for api_key {api_key}")
        return False

    l_ips = d_ips.get(api_key,None)
    if l_ips and len(l_ips) > 0:
        ### check if IP is whitelisted
        if not orig_ip in l_ips and not orig_ip in whitelist_ip:
            logger.warning(f"{orig_ip} is not whitelisted for {api_key}")
            return False

        ### IP is whitelisted, now check if api_secret match
        expected_secret = d_secret.get(api_key,None)
        if expected_secret:
            logger.debug(f"debug: account found with api_key {api_key}, expected_secret: {expected_secret}, received_secret: {credentials.password}")
            password_match = secrets.compare_digest(credentials.password, expected_secret)

            if password_match:
                ac = {"api_key": api_key}
                return ac
            else:
                logger.warning("!!! api_secret does not match")
        else:
            logger.warning("!!! no api_secret found")

    return False

### /api/test/sms: for developer to test create-sms from UI, no soure IP checking because browser IP can be anything. no sms will be created
def myauth_basic_authfile_test(request:Request, credentials: HTTPBasicCredentials = Depends(security)):
    orig_ip = request.client.host
    api_key = credentials.username

    d_secret,d_ips = read_auth()
    logger.debug(f"debug: client IP: {orig_ip}, d_secret: {json.dumps(d_secret, indent=4)}, d_ips: {json.dumps(d_ips, indent=4)}")

    if not api_key in d_secret:
        logger.warning("debug: no account found for api_key {api_key}")
        return False
    
    ### no IP checking, only check if api_secret match
    expected_secret = d_secret.get(api_key,None)
    if expected_secret:
        logger.debug(f"debug: account found with api_key {api_key}, expected_secret: {expected_secret}, received_secret: {credentials.password}")
        password_match = secrets.compare_digest(credentials.password, expected_secret)

        if password_match:
            ac = {"api_key": api_key}
            return ac
        else:
            logger.warning("!!! api_secret does not match")
    else:
        logger.warning("!!! no api_secret found")


    return False


def myauth_jwt():
    return False

#async def authenticate(basic_result=Depends(myauth_basic), jwt_result=Depends(myauth_jwt)):
async def authenticate(basic_result=Depends(myauth_basic_authfile), jwt_result=Depends(myauth_jwt)):
    if not (basic_result or jwt_result):
        logger.debug("debug: none of the auth method pass")

        raise HTTPException(
            status_code=401, #unauthorized
            detail="Incorrect credential or non-whitelisted IP"
        )
    else:
        logger.debug("debug: one of the auth method pass")
        if basic_result:
            return basic_result
        else:
            return jwt_result

async def authenticate_test(basic_result=Depends(myauth_basic_authfile_test), jwt_result=Depends(myauth_jwt)):
    if not (basic_result or jwt_result):
        logger.debug("debug: none of the auth method pass")

        raise HTTPException(
            status_code=401, #unauthorized
            detail="Incorrect credential or non-whitelisted IP"
        )
    else:
        logger.debug("debug: one of the auth method pass")
        if basic_result:
            return basic_result
        else:
            return jwt_result


async def allowinternal(request: Request):
    client_ip = request.client.host
    if not request.client.host in whitelist_ip:
        raise HTTPException(status_code=401, detail=f"unauthorized access")


if __name__ == '__main__':
    read_auth()
