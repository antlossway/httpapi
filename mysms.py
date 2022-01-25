import os
import mydb # r => redis connector, cur => postgres
from uuid import uuid4
import requests
import json
import re

from myutils import logger,config
from mydb import g_numbering_plan

notif1_expire = 5*24*3600 #redis: MSGID2:msgid2 => msgid1:::api_key:::require_dlr

"""
example request and response body to be displayed in API document
"""
example_create_sms={
    "normal": {
        "summary": "example with short SMS",
        "description": "Short SMS, 1-part, max 160 GSM-7bit characters or max 70 USC-2 encoded caracters",
        "value": {
            "from": "NOC",
            "to": "971508020179",
            "content": "Hello World!"
        },
    },
    "concatenated": {
        "summary": "example with long SMS",
        "description": "Concatenated SMS,a long SMS segemented into mutliple part,each part is charged as separate SMS",
        "value": {
            "from": "NOC",
            "to": "971508020179",
            "content": "A man being mugged by two thugs put up a tremendous fight! Finally, the thugs subdued him and took his wallet. Upon finding only two dollars in the wallet, the surprised thug said \"Why did you put up such a fight?\" To which the man promptly replied \"I was afraid that you would find the $200 hidden in my shoe!\""
        },
    },
}

example_create_sms_response = {
    200: {
        "description": "success",
        "content":{
            "application/json":{
                "examples":{
                    "normal": {
                        "summary": "example with short SMS",
                        #"description": "Short SMS, 1-part, max 160 GSM-7bit characters or max 70 USC-2 encoded caracters",
                        "value": {
                            "errorcode": 0,
                            "message-count": 1,
                            "messages": [
                                {
                                    "msgid": "77b16382-7871-40bd-a1ac-a26c6ccce687",
                                    "to": "971508020179"
                                }
                            ]
                        },
                    },
                    "concatenated": {
                        "summary": "example with long SMS",
                        #"description": "Concatenated SMS,a long SMS segemented into mutliple part,each part is charged as separate SMS",
                        "value": {
                            "errorcode": 0,
                            "message-count": 3,
                            "messages": [
                                {
                                    "msgid": "77b16382-7871-40bd-a1ac-a26c6ccce687",
                                    "to": "971508020179"
                                },
                                {
                                    "msgid": "9d316085-cc29-4fb6-9522-6ad8748fcb89",
                                    "to": "971508020179"
                                },
                                {
                                    "msgid": "def6196e-3b73-4a1a-9d1b-f46cbf139645",
                                    "to": "971508020179"
                                }
                            ]
                        },
                    },
                },
            },
        },
    },
}

### legacy code logic on a2p server
def create_sms_file(acname,sender,to,xms,msgid,dcs,udh,require_dlr):
    uc_acname = acname.upper()
    outdir = "/tmp/sms/" + uc_acname
    output = os.path.join(outdir, f"xms{msgid}")
    tmpoutput = os.path.join(f"{outdir}/tmp", f"xms{msgid}")
    error = 0
    notif1_dir = "/tmp/notif1"
    

    """create notif1 file, empty file"""
    notif1 = f"{notif1_dir}/{uc_acname}/{uc_acname}---{to}---{msgid}---"
    logger.info(f"create notif1 {notif1}")
    try:
        with open(notif1,'w') as w:
            pass
    except IOError as e:
        logger.info(e)
        error = 1001
    except:
        logger.info(f"something bad happen,can not create {tmpoutput}")
        error = 1001
    if error != 0:
        return error


    """create SMS file"""
    try:
        with open(tmpoutput,'w', encoding='utf-8') as w:
            w.write("; encoding=UTF-8\n")
            w.write(f"[{acname.upper()}]\n")
            w.write(f"DCS={dcs}\n")
            w.write(f"Phone={to}\n")
            w.write(f"OriginatingAddress={sender}\n")
            w.write(f"LocalId={msgid}\n")
            w.write(f"MsgId={msgid}\n")
            w.write(f"XMS={xms}\n")
            w.write(f"StatusReportRequest=True\n") #always require DLR from our supplier

            if udh != '':
                w.write(f"UDH={udh}\n")
        os.rename(tmpoutput,output)
        logger.info(f"created {output}")
    except IOError as e:
        logger.info(e)
        error = 1001
    except:
        logger.warning(f"something bad happen,can not create {tmpoutput}")
        error = 1001
    finally:
        return error


def create_sms_ameex(ac,data,provider):

    sender = data.get('sender')
    msisdn = data.get('to')
    xms = data.get('content')
    msgid1 = data.get('msgid')
    require_dlr = data.get('require_dlr')
    udh = data.get('udh')

    api_key,api_secret = config['provider_api_credential'].get(provider).split('---')
    logger.debug(f"debug provider_api_credential for {provider}: {api_key} {api_secret}")

    req_data = {
        "from": sender,
        "to": msisdn,
        "content": xms,
        'udh': udh
    }

    url = f"https://{api_key}:{api_secret}@a2p.ameex-mobile.com/api/sms"
    res = requests.post(url,json=req_data, timeout=(2,10)) #tuple:1st num means the timeout when client establish connection to the server, 2nd num means the timeout to get response(connection already established)
    res_json = res.json()
    logger.info("### Response from provider:")
    logger.debug(json.dumps(res_json,indent=4))

    res_error = int(res_json.get('errorcode',0))
    res_split = int(res_json.get('message-count',1)) #should always be 1 because I already do split, and call provider API for each part
    res_msgid = res_json.get('messages')[0].get('msgid','')
    
    if res_split != 1:
        logger.warning(f"!!! split result different from AMEEX {res_split}")

    ### TBD: redis pipeline
    ### record MSGID2:<msgid2> => <msgid1>:::<api_key>:::<require_dlr>, for callback_dlr to map msgid1 and callback_url of client
    k = f"MSGID2:{res_msgid}"
    v = f"{msgid1}:::{api_key}:::{require_dlr}"
    mydb.r.setex(k,notif1_expire, value=v)
    logger.info(f"SETEX {k} {notif1_expire} {v}")

    ### record MSGID1:<msgid1> => <msgid2>, for API endpoint /sms/:msgid1 to query_dlr, check if msgid1 exists
    k = f"MSGID1:{msgid1}"
    v = res_msgid
    mydb.r.setex(k,notif1_expire, value=v)
    logger.info(f"SETEX {k} {notif1_expire} {v}")

    return res_error,res_msgid
    

## call HTTP API on a2p server
def create_sms(ac,data,provider): #ac: dict inclues account info, data: dict includes sms info
    #     ac = {
    #     "api_key": api_key,
    #     "api_secret": api_secret,
    #     "billing_id": billing_id,
    #     "company_name": company_name,
    #     "webuser_id": webuser_id,
    #     "webuser_name": webuser_name,
    #     "product_id": product_id,
    #     "product_name": product_name,
    #     "api_credential_id": api_credential_id
    #     }

    #     data = {
    #     "msgid": msgid,
    #     "sender": sender,
    #     "to": msisdn,
    #     "content": xms,
    #     "require_dlr": require_dlr,
    #     "country_id": country_id,
    #     "operator_id": operator_id,
    #     "udh": udh,
    #     "dcs": dcs,
    #     "cpg_id": cpg_id #send_campaign.py will call api/internal/sms
    # }

    logger.info(f"debug: account {ac}")
    webuser_id = ac.get('webuser_id')
    billing_id = ac.get('billing_id')
    product_id = ac.get('product_id')
    api_credential_id = ac.get('api_credential_id')

    error = 0
    msgid = data.get('msgid')
    sender = data.get('sender')
    bnumber = data.get('to')
    xms = data.get('content')
    country_id = data.get('country_id')
    operator_id = data.get('operator_id')
    udh = data.get('udh','')
    cpg_id = data.get('cpg_id',0)
    dcs = data.get('dcs',0)
    msgid2 = ''

    sender = re.sub(r"'",r"''",sender)
    xms = re.sub(r"'",r"''",xms)

    if provider.startswith("AMEEX"):
        create_error, msgid2 = create_sms_ameex(ac,data,provider)
    else:
        pass #other provider API connector

    if create_error == 0 and msgid2 != None and msgid2 != '': #SMS successfully submitted to provider
        #record into redis cdr_cache
        if cpg_id != 0:
            sql = f"""insert into cdr (webuser_id,billing_id,product_id,msgid,notif3_msgid,tpoa,bnumber,country_id,operator_id,
            dcs,len,udh,xms,cpg_id,api_credential_id) values ({webuser_id},{billing_id},{product_id},'{msgid}','{msgid2}','{sender}','{bnumber}',
            {country_id},{operator_id},{dcs},{len(xms)},'{udh}','{xms}',{cpg_id},{api_credential_id});"""
        else:
            sql = f"""insert into cdr (webuser_id,billing_id,product_id,msgid,notif3_msgid,tpoa,bnumber,country_id,operator_id,
            dcs,len,udh,xms,api_credential_id) values ({webuser_id},{billing_id},{product_id},'{msgid}','{msgid2}','{sender}','{bnumber}',
            {country_id},{operator_id},{dcs},{len(xms)},'{udh}','{xms}',api_credential_id);"""

        logger.info(sql)
        if mydb.r.lpush('cdr_cache',sql): #successful transaction return True
            logger.info(f"LPUSH cdr_cache OK")
        else:
            logger.warning(f"!!! problem to LPUSH cdr_cache {sql}")
    else: # problem in outgoing route
        error = 2000
    
    return error


def clean_msisdn(msisdn):
    number = msisdn.strip() #remove trailing whitespaces
    number = re.sub(r'^\++', r'',number) #remove leading +
    number = re.sub(r'^0+', r'',number) #remove leading 0

    if re.search(r'\D+',number): #include non-digit
        return None

    number = re.sub(r'^',r'+',number) #add back leading +

    if len(number) < 11 or len(number) > 16:
        return None

    return number

def parse_bnumber(np,msisdn):
  result = None
  while len(msisdn) > 0:
    if msisdn in np.keys():
      result = np[msisdn]
      break
    else:
      msisdn = msisdn[:-1]  #remove last digit
  return result

    
if __name__ == '__main__':
    # msgid = str(uuid4())
    # ac = {
    #     'webuser_id': 1,
    #     'billing_id': 1,
    #     'product_id': 0
    # }

    # data = {
    #     "msgid": msgid,
    #     "sender": "NOC",
    #     "to": "+6586294138",
    #     "content": "hello world!",
    #     "country_id": 95,
    #     "operator_id": 95,
    # }
    # error,msgid2 = create_sms_ameex(ac,data,'AMEEX_PREMIUM')
    # print(f"debug call result: {error}, {msgid2}")

    result = parse_bnumber(g_numbering_plan, '+089')
    if result:
        cid,opid = result.split('---')
        print(cid,opid)

