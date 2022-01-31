#!/usr/bin/perl
use strict;
use FindBin qw($Bin);
use lib "$Bin/../perllib";
use ConnectDB;

my ($yesterday,$today);

my $arg_date = $ARGV[0]; #2019-08-15
my $table = "cdr_agg";

if($arg_date eq "")
{
	($yesterday,$today) = split(/---/,&get_timestamp());
	print "insert yesterday $yesterday 's traffic into $table\n";
}
else
{

	print "insert $arg_date 's traffic into $table\n";
}

my $db = connectdb();

my $now = localtime();
print "===== process at $now =====\n";

my $start_time = time();

my $sql_result;
if($arg_date ne "")
{
	$sql_result = exec_sql($db,"insert into cdr_agg (date,billing_id,account_id,product_id,country_id,operator_id,provider_id,cpg_id,tpoa,status,sum_split,sum_sell) select date(dbtime) as date,billing_id,account_id,product_id,country_id,operator_id,provider_id,cpg_id,tpoa,status,sum(split), sum(selling_price) from cdr where dbtime >= '$arg_date' and date(dbtime) = '$arg_date' group by date,account_id,billing_id,product_id,country_id,operator_id,provider_id,cpg_id,tpoa,status order by date;");

}
else
{
	$sql_result = exec_sql($db,"insert into cdr_agg (date,billing_id,account_id,product_id,country_id,operator_id,provider_id,cpg_id,tpoa,status,sum_split,sum_sell) select date(dbtime) as date,billing_id,account_id,product_id,country_id,operator_id,provider_id,cpg_id,tpoa,status,sum(split), sum(selling_price) from cdr where dbtime >= current_date - interval '1 day' and dbtime < current_date group by date,account_id,billing_id,product_id,country_id,operator_id,provider_id,cpg_id,tpoa,status order by date;");
}

print "insert into $table\n";
print "--" . $sql_result -> rows()."\n";

my $end_time = time();
my $delay = $end_time - $start_time;
print "insert $table took $delay sec\n";

sub get_timestamp
{
        my $cmd1 = "date +%Y-%m-%d --date=\"yesterday\"";
        my $cmd2 = "date +%Y-%m-%d";

        open(DATE, "$cmd1|");
        my $yesterday = <DATE>;
        chomp($yesterday);
        close(DATE);

        open(DATE, "$cmd2|");
        my $today = <DATE>;
        chomp($today);
        close(DATE);
	

	return "$yesterday---$today";
}

$db ->disconnect();
exit(0);
