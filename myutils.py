import random
import logging
import time
import os
from configparser import ConfigParser

basedir = os.path.abspath(os.path.dirname(__file__))
log = basedir + '/log/' + 'httpapi.log'
config_file = basedir + '/' + '.config'

def read_config():
    config = ConfigParser()

    logger.info(f"======= read_config {config_file}=======")
    ### reinitialize
    for section in config.sections():
        config.remove_section(section)

    config.read(config_file)

    for section in config.sections():
        logger.info(f"#### {section} ####")
        for key,value in config[section].items():
            logger.info(f"{key} => {value}")

    logger.info("===============================")
    return config

########################
### Logging          ###
########################

logger = logging.getLogger(__name__)
#logger.setLevel(logging.INFO)
logger.setLevel(logging.DEBUG)
logging.Formatter.converter = time.gmtime
# create a console handler
c_handler = logging.StreamHandler()
c_handler.setLevel(logging.INFO)
# create a file handler
handler = logging.FileHandler(log)
# handler.setLevel(logging.INFO)
handler.setLevel(logging.DEBUG)

# create a logging format
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
# add the handler to the logger
logger.addHandler(handler)
logger.addHandler(c_handler)



########################
### Configurations   ###
########################

config = read_config() # read into config object

def gen_udh_base():
        rand1 = random.randint(0,15)
        rand2 = random.randint(0,15)

        udh_base = "0003" + format(rand1,'X') + format(rand2, 'X')
        return udh_base
def gen_udh(udh_base,split,i):
    return udh_base + format(split,'02d') + format(i,'02d')


if __name__ == '__main__':
    udh_base = gen_udh_base()
    print(udh_base)