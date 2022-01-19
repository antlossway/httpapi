# SMS HTTP API

This is a backend for white-label SMS platform using fastAPI.


Install database
- postgresql
- redis-server

Setup FastAPI
====================
1. set up virtual environment
python3 -m venv venv
source venv/bin/activate

2. update pip
easy_install -U pip

3. install dependencies
pip install -r requirements.txt 

4. create .config, sample:
[redis]
host=localhost
port=6379

[postgresql]
host=localhost
port=5432
db=database name
user=database user
password=xxxxx

[provider_api_credential]
provider_name=api_key---api_secret

