import psycopg2
import redis
import os
from collections import defaultdict
import json

from myutils import config,logger

########################
### Global Variables ###
########################
userinfo = defaultdict(dict) #info from legacy table 
d_account = defaultdict(dict) #info from new tables

# basedir = os.path.abspath(os.path.dirname(__file__))
# config_dir = basedir + "/" + "config/"
# redis_cfg = config_dir + "redis.cfg"
db_host = config['postgresql']['host']
db_name = config['postgresql']['db']
db_user = config['postgresql']['user']
db_pass = config['postgresql']['password']

try:
    db = psycopg2.connect(host=db_host,database=db_name, user=db_user, password=db_pass)
    db.autocommit = True
except Exception as error:
    logger.info(f"DB connection failed: {error}")

logger.info("DB connected")
cur = db.cursor()

#### redis ####
# def read_redis_config():
#     d = dict()
#     with open(redis_cfg, 'r',encoding='utf-8') as f:
#         lines = f.readlines()
#         for line in lines:
#             (k,v) = line.strip().split('=')
#             if k == 'host':
#                 d[k] = v
#             elif k == 'port' or k == 'db':
#                 d[k] = int(v)
#     return d

# d_redis = read_redis_config()
# for k,v in d_redis.items():
#     logger.info(f"redis param: {k} => {v} ({type(v)})")

# r = redis.Redis(host=f"{d_redis['host']}",port=f"{d_redis['port']}")
redis_host = config['redis']['host']
redis_port = config['redis']['port']

r = redis.Redis(host=redis_host,port=redis_port)

try:
    r.ping()
    logger.info("redis server connected")
except:
    logger.info("!!! Can not connect redis server, leave")
    exit()

### legacy code for authentication
# cur.execute("select h.customerid,c.name,h.api_key,h.api_secret_enc,h.salt,c.directory,c.currency from http_customers h,customers c where h.customerid=c.id;")
# rows = cur.fetchall()
# for row in rows:
#     (acid,acname,api_key,api_secret_enc,salt,dir,currency) = row
#     #logger.info(acid,api_key,api_secret_enc,salt,dir,currency)

#     userinfo[api_key]['secret_enc'] = api_secret_enc
#     userinfo[api_key]['salt'] = salt
#     userinfo[api_key]['name'] = acname
#     userinfo[acname]['dir'] = dir
#     userinfo[acname]['customerid'] = acid
#     userinfo[acname]['currency'] = currency

# for k,v in userinfo.items():
#     print(k,v)

cur.execute("""select api_key,api_secret, b.id as billing_id, b.company_name, webuser_id, w.username as webuser_name,product_id,
     p.name as product_name, callback_url from api_credential a join webuser w on w.id=a.webuser_id
     join billing_account b on w.billing_id = b.id join product p on p.id=a.product_id;""")
rows = cur.fetchall()
for row in rows:
    (api_key,api_secret,billing_id,company_name,webuser_id,webuser_name,product_id,product_name, callback_url) = row
    ac = {
        "api_key": api_key,
        "api_secret": api_secret,
        "billing_id": billing_id,
        "company_name": company_name,
        "webuser_id": webuser_id,
        "webuser_name": webuser_name,
        "product_id": product_id,
        "product_name": product_name,
        "callback_url": callback_url
    }
    d_account[api_key] = ac

logger.info("### print all ap_credentials")
for api_key,ac in d_account.items():
    logger.info(f" - {api_key}")
    logger.info(json.dumps(ac, indent=4))
    
