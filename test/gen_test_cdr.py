#!/usr/bin/evn python3
import random

l_status = ['DELIVRD','EXPIRED','REJECTD','UNDELIV']


status = random.choices(l_status,weights=[90,1,2,7])

sql = f"""insert into cdr (dbtime,webuser_id,billing_id,product_id,msgid,tpoa,bnumber,country_id,operator_id,xms,status,provider_id)
        values ('{ts}',);
    """



