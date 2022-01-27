#!/usr/bin/perl
use strict;
use lib '/home/amx/perllib';
use MyLog;
use ConnectDB;
use RedisNotif;
use Monitor;
use MyTool qw/file_age query_mnp/;
use MyTime;
use Data::Dumper;
use POSIX ":sys_wait_h";

my $sendxmsdir = "/home/amx/sendxms";
my %nocheckdir;
$nocheckdir{'pid'} = 1;
$nocheckdir{'test'} = 1;
$nocheckdir{'TESTSERVER'} = 1;
$nocheckdir{'TESTST'} = 1;
$nocheckdir{'china'} = 1;
$nocheckdir{'france'} = 1;
$nocheckdir{'singapore'} = 1;
$nocheckdir{'SERVER'} = 1;
$nocheckdir{'CLIENT'} = 1;

my $cfg = "/home/amx/etc/notif3gen.cfg";
my %map;
my $localid_dir = "/usr/local/SendXMS/localids";
my $lock = "/home/amx/var/lock/notif3gen.lock";
my $trashdir = "/home/amx/trash";
my $nodlr_dir = "/home/amx/nodlr"; #will not return customer the DLR if exists filename with this msgid
my $batch = 10000;
my $age_dlr = 300; #if dlr file older than 5min, and localid file not found, will move dlr to notif3_dup

my $sql_dir = "/home/amx/sql";
my $sql_tmpdir = "/home/amx/sql/tmp";

my $sql_dir_ethio = "/home/amx/sql_ethio";
my $sql_tmpdir_ethio = "/home/amx/sql_ethio/tmp";

my $notif1_redis_expire = 3600*24*3; #delete DLR if it comes after 3 days when notif1 info in redis already expired

my $redis_dir = "/home/amx/redis_dlr"; #map externalID with main_localid

my %customer_notif3_dir; #record where to send DLR for each customer
my %customer_require_dlr;
my %smsc_name_id;
my %h_smsc_provider;
my $load_cfg = "/home/amx/etc/max_load";

$SIG{'TERM'} = "mark_terminate";
$SIG{'INT'} = "mark_terminate";
$SIG{'QUIT'} = "mark_terminate";
$SIG{'USR1'} = "mark_dbupdate";

my $mark_terminate = 0;
my $mark_dbupdate = 0;
my %pid;

sub mark_terminate
{
	$mark_terminate = 1;
}
sub mark_dbupdate
{
	$mark_dbupdate = 1;
}

my $mccmnc_map = [
[ '42001' => qr/^9665(?:0|3|5)/o ],
[ '42005' => qr/^96657(?:0|1|2)/o ],
[ '42004' => qr/^9665(?:8|9)/o ],
[ '42003' => qr/^9665(?:4|6)/o ],
[ '42006' => qr/^96657(?:6|7|8)/o ],
];

sub get_mccmnc
{
	my $msisdn = shift;
	$msisdn =~ s/^\+//;
	for my $op ( @{$mccmnc_map} )
	{
		return $op->[0] if $msisdn =~ $op->[1];
	}
}

sub save_sql
{
	my $sql = shift;
	my $customername = shift;

	my $rand = $$."-".rand(1000000)."-".time();
	my $tmp_file = $sql_tmpdir ."/.". $rand;
	my $file = $sql_dir ."/" . $rand .".sql";
	mylog("$sql\n");

	open(OUT,">$tmp_file") or return undef;
	print OUT $sql .";\n";
	close(OUT);
	rename($tmp_file,$file);

	if (defined $customername and $customername =~ /^ETHIO/i)
	{
		my $tmp_file = $sql_tmpdir_ethio ."/.". $rand;
		my $file = $sql_dir_ethio ."/" . $rand .".sql";
		unless(open(OUT2,">$tmp_file"))
		{
			mylog("Can not open $tmp_file: $!\n");
			return undef;
		}
		print OUT2 $sql .";\n";
		close(OUT2);
		rename($tmp_file,$file);
		#mylog("### save_sql again $file\n");
	}

	return 1;
}

sub trash_mo
{
	my $inputfile = shift;
	my $outdir = shift;
	my $filename = $inputfile;
	$filename =~ s/.*\/(.*)$/$1/g;
	my $outputfile;
	if($outdir ne "" and -d $outdir)
	{
		$outputfile = "$outdir/$filename";
	}
	else
	{
		mkdir $trashdir unless -d $trashdir;
		$outputfile = "$trashdir/$filename";
	}
	rename($inputfile,$outputfile);
	mylog("trash MO file $inputfile to $outputfile\n");
}

sub leave
{
	if(-e $lock)
	{
		unlink($lock);
	}
	exit(0);
}

sub check_signal
{
	unless($mark_terminate == 0)
	{
        while(my($pid,undef) = each(%pid))
        {
            kill(15,$pid);
        }
		mylog("receive TERM signal, will leave...\n");
		leave();
	}

	unless($mark_dbupdate == 0)
	{
		hash_update();
		$mark_dbupdate = 0;
	}
}

sub get_max_load
{
	open(LOADCFG, "<$load_cfg");
	my $line = <LOADCFG>;
	close(LOADCFG);
	chomp($line);
	mylog("debug: max load is $line\n");
	return $line;
}

sub check_load
{
	my $loadfile = "/proc/loadavg";

	unless(open(LOAD, "<$loadfile"))
	{
		mylog("!!! can't open $loadfile: $!\n");
		return undef;
	}

	my $line = <LOAD>;
	close(LOAD);

	chomp($line);
	my @info = split(/ /,$line);
	my $load = $info[0];
	$load = int($load);
	return $load;
}

sub get_timestamp
{
	my @date = localtime();
	my $year = $date[5] + 1900;
	my $month = sprintf('%02d',$date[4] + 1);
	my $day = sprintf('%02d',$date[3]);
	my $hour = sprintf('%02d',$date[2]);
	my $minute = sprintf('%02d',$date[1]);
	my $second = sprintf('%02d',$date[0]);

	my %timestamp;
	$timestamp{'created'} = $year.$month.$day.$hour.$minute.$second;
	$timestamp{'submit'} = substr($year,2,2).$month.$day.$hour.$minute;
	$timestamp{'date'} = "$year.$month.$day $hour:$minute:$second +0000";
	return %timestamp;
}

sub local_continue
{
	my $dir = shift;
	mylog("Check LCONT $sendxmsdir/$dir/.no-notif\n");
	return 0 if -f    "$sendxmsdir/$dir/.no-notif";

	#return 0 if($dir =~ /^SERVER/ or $dir =~ /MEO\d/); # MEO is customer, but we have to act as ESME, their use deliver_sm to send SMS to us
	return 0 if($dir =~ /^SERVER/ or $dir =~ /^CLIENT/); 

	#$dir =~ s/\d+$//;

	return 0 if($nocheckdir{$dir} == 1);
	return 1;
}

sub checkdir
{
	my $dir = shift; #/home/amx/sendxms/TELESTAX/received/TELESTAX
	my $provider = shift; #TELESTAX

	my $count = 0;
	if(opendir(DIR, "$dir"))
	{
		while(my $file = readdir(DIR))
		{
			chomp($file);
			if($file =~ /^xms/)
			{
				create_notif3("$dir/$file",$provider);
				$count++;
			}
			last if $count > $batch;
		}
		close(DIR);
	}
	else
	{
		mylog("!!!checkdir: can't open $dir: $!\n");
		return 0;
	}
	return $count;	
}

sub matchid
{
	my $provider = shift;
	my $msgid = shift;
	my $bnumber = shift;

	#mylog("search localid in $localid_dir for $provider: $msgid.$bnumber\n");

	my (@tocheck, $localid,$dir,%result);
	$result{'localid'} = "";
	$result{'file'} = "";

	$provider =~ s/\d+$//; #equal to below 2 lines
	#$provider =~ s/(.*)\d+$/$1/; #remove end digit if there is any
	#$provider =~ s/(.*)\d+$/$1/; #in case there are 2 digits at the end: STC10 -> STC

#	if($provider =~ /SAP/ or $provider =~ /BRAVO/) #localid dir record file name with hex of msgid
#	{
#		#mylog("### debug: DLR convert decimal msgid into Hex\n");
#		$msgid =~ s/^0//;
#		$msgid = sprintf("%x", $msgid);
#	}

	if (opendir(LOCALID, $localid_dir))
	{
		while(my $dir = readdir(LOCALID))
		{
			chomp($dir);
			if($dir =~ /$provider/)
			{
				push(@tocheck, $dir);
			}
		}

		close(LOCALID);
	}
	else
	{
		mylog("!!! matchid: can't open $localid_dir: $!\n");
		exit(0);
	}

	my $record;
	my $found = 0;

	foreach my $item (@tocheck)
	{
		$dir = "$localid_dir/$item";
		$record = "$dir/$msgid.$bnumber";
		last if(-e $record);
	}

	if(! -e $record)
	{
		mylog("!!! catch pattern: maybe localid file not yet created for $provider $msgid.$bnumber\n");
	}
	elsif( -z $record)
	{
		mylog("!!! catch pattern: localid file $record size 0\n");
	}
	else
	{
		#	mylog("found localid file $record\n");
		open(LOCALIDFILE, "<$record") or die "can't open $record: $!\n";
		while(my $line = <LOCALIDFILE>)
		{
			chomp($line);
			next if $line !~ /^LocalId/;

			if($line =~ /LocalId=(.*)/)
			{
				$localid = $1;
			}
		}
		close(LOCALIDFILE);
		mylog("localid in $record: $localid\n");

		if($localid eq "")
		{
			mylog("!!! catch pattern: maybe localid file creation not finished $msgid.$bnumber\n");
			#sleep(1);
		}
		else
		{
			$result{'localid'} = $localid;
			$result{'file'} = $record;
			return %result;
		}
	}

	if($localid eq "")
	{
		mylog("!!! catch pattern: matchid return empty result for $msgid.$bnumber for $provider\n"); 
		return %result;
	}
}

sub get_customer_localid
{
	my $bnumber = shift;
	my $localid = shift;
	my $keepid = shift;

	$keepid = 0 if $keepid eq ""; #by default will delete partid info, keep is set to 1 when it's longSMS and need to retry

	my $clocalid = $localid;

	my ($info_ref, $part_ref) = notif_redis($bnumber, $localid);

	my %info = %$info_ref;
	my %part = %$part_ref;

	while(my ($key,$value) = each %info)
	{
		mylog("debug notif_redis info $key => $value\n");
	}

	while(my ($key,$value) = each %part)
	{
		mylog("debug legacy notif_redis part $key => $value\n");
	}

	my $cname = $info{"CUSTOMER"};
	my $bnumber_partid = $info{"PART"};

	if ($bnumber_partid ne "") #long sms
	{
		my @ids = keys %part;
		my $size = scalar(@ids);
	
		if($size > 0) #long sms, need to get a customer localid
		{
			#select one of localid from %part to return to customer
			$clocalid = pop(@ids);
			my $res = delete_part($bnumber_partid, $clocalid);
			mylog("($$): redis remain $size ids, hdel($bnumber_partid, $clocalid) => $res\n");
			$size --;

			if ($res <= 0 and $size > 0)
			{
				$clocalid = pop(@ids);
				my $res = delete_part($bnumber_partid, $clocalid);
				mylog("($$): redis remain $size ids, try to delete another key hdel($bnumber_partid, $clocalid) => $res\n");
			}
		}
		else #long sms, but all parts already generated DLR
		{
			mylog("catch pattern: possible duplicate, but I still generate DLR\n");
		}

	}

	return "$clocalid---$cname";
}

sub create_notif3
{
	my $dlr = shift; #/home/amx/sendxms/TELESTAX/received/TELESTAX/xmsXXXXXXXXX
	my $provider = shift; #TELESTAX
	my ($bnumber,$tpoa,$msgid,$localid,$addinfo,$addcode,$sms,$localidfile);
	my $default_tpoa = "+12340000"; #default TPOA
	my ($orig_tpoa,$orig_bnumber);
	my $submit_date; #used to delete expired DLR

#; encoding=UTF-8
#[AMEEX_PREMIUM1]
#Created=20220127104128
#Action=Status
#UsedDevice=network
#UsedProtocol=SMPP
#Date=2022.01.27 10:41:00 +0000
#Phone=5:0:FACEBOOK
#OriginatingAddress=+6586294138
#MsgId=61F276D3-35668-664E-7F9FFC889700
#Priority=0
#AddInfo=2201271041   [[[2;220127104100]]]
#AddCodes=2; 000
#XMS=id:61F276D3-35668-664E-7F9FFC889700 sub:001 dlvrd:001 submit date:2201271041 done date:2201271041 stat:DELIVRD err:000 text:

	mylog("process $dlr\n");
	mylog("===================================\n");

	my $to_trash = 0;

	if(open(DR, "<$dlr"))
	{
		while(my $line = <DR>)
		{
			mylog($line);
			chomp($line);
			if($line =~ /Action=Receive/) #MO file
			{
				$to_trash = 1;
			}

			if($line =~ /Phone=(.*)/) #normally this field is TPOA in subimtted SMS
			{
				$tpoa = $1;
				$orig_tpoa = $tpoa;

				my $replace_count = $tpoa =~ s/^\++//;
				my $len_tpoa = length($tpoa);

				if($tpoa =~ /\D/) #TPOA from customer is alphanumeric, add TON:NPI to be able to deliver DLR
				{
					if($tpoa !~ /\d:\d:/)
					{
						$tpoa = "5:0:$tpoa";
						mylog("debug: add 5:0: to $tpoa\n"); 
					}
				}
				elsif($tpoa eq "")
				{
					$tpoa = "$default_tpoa";
					mylog("debug: empty TPOA is replaced by $tpoa\n"); 
				}
				else #only digits
				{
					#if($len_tpoa >= 9) #TPOA is likely in MSISDN format
					if($len_tpoa >= 9 or $replace_count == 1) #orig tpoa start with +
					{
						$tpoa =~ s/^00//;
						$tpoa =~ s/^/+/;
						mylog("debug: add back begin + to $tpoa\n"); 
					}
				}
			}
			
			if($line =~ /OriginatingAddress=(.*)/) #normally this field is B-number in submitted SMS
			{
				$bnumber = $1;
				$bnumber =~ s/^\d:\d://g; #DOTT return us 5:0:79697530263
				$orig_bnumber = $bnumber;

				my $replace_count = $bnumber =~ s/^\++//;
				my $len_bnumber = length($bnumber);

				if($bnumber =~ /\D/) #some supplier reverse Bnumber and TPOA
				{
					if($bnumber !~ /\d:\d:/)
					{
						$bnumber = "5:0:$bnumber";
						mylog("debug: add 5:0: to $bnumber\n"); 
					}
				}
				else
				{
					$bnumber =~ s/^000//; #Take care of linkmobility DLR
					$bnumber =~ s/^00//;
					if($len_bnumber >= 9 or $replace_count == 1)
					{
						$bnumber =~ s/^/+/;
						#mylog("debug add back begin + to $bnumber\n"); 
					}
				}
			}
			#this localid is inside provider's DLR, for longsms, it will be same for each parts's DLR, need to search clocalid to return to customer
			elsif($line =~ /LocalId=(.*)/) 
			{
				$localid = $1;
			}
			elsif($line =~ /MsgId=(.*)/)
			{
				$msgid = $1;
			}
			elsif($line =~ /AddInfo=(.*)/)
			{
				$addinfo = $1;
			}
			elsif($line =~ /AddCodes=(.*)/)
			{
				$addcode = $1;
			}
			elsif($line =~ /XMS=(.*)/)
			{
				$sms = $1;
				
				if($line =~ /ACKED/ or $sms =~ /BUFFRED/ or $sms =~ /ENROU/ or $sms =~ /ACCEP/)
				{
					mylog("$dlr has not final status, will not create notif3, remove it\n");
					close(DR);
					unlink($dlr);
					goto END_NOTIF3;
				}

				if ($sms =~ /submit date:(\d+)/)
				{
					$submit_date = $1;
				}
			}
		}
		close(DR);
	}
	else
	{
		mylog("!!!create_notif3: can't open $dlr: $!\n");
		goto END_NOTIF3;
	}

	if($to_trash == 1)
	{
		trash_mo($dlr);
		return undef;
	}

	if($msgid ne "")
	{
		if($localid eq "") #some provider don't include localid in DLR, need to search in /usr/local/SendXMS/localids/[provider]
		{
			my %result;
			%result = matchid($provider,$msgid,$bnumber);
			$localidfile = $result{'file'};
			if($localidfile eq "")
			{
				if($provider =~ /MONTY/ or $provider =~ /ALKAIP/)
				{
					mylog("catch pattern: $provider try reverse TPOA $tpoa and B-number $bnumber to match localid file\n");
					%result = matchid($provider,$msgid,$tpoa);
					$localidfile = $result{'file'};
					if($localidfile ne "")
					{
						my $buffer = $tpoa;
						$tpoa = $bnumber;
						$bnumber = $buffer,
					}
				}
				elsif($provider =~ /^STC/)
				{
					if(file_age($dlr) < $age_dlr) #give 5 min delay for localid file to be created
					{
						mylog("catch pattern: maybe $provider localid file not yet created, wait for next round\n");
						goto END_NOTIF3;
					}
				}
			}	
            
			my $filename = $dlr;
			$filename =~ s/.*\/(.*)/$1/;
			my $dlr_dup = "/home/amx/notif3_dup/$filename";
			mkdir $dlr_dup unless -d $dlr_dup;
			#debug
 			#mylog("debug localid found: $localid\n");

			$localid = $result{'localid'};
	
			if($localid eq "") #no matching localid file, possible duplicate notif3
			{
				#if($provider =~ /HIGHCONNEXION/)
				#if($provider =~ /SIGNATEL/)
				#{
				#	unlink($dlr);
				#	mylog("delete $provider DLR $dlr, their bug\n");
				#}
				#else
				#{
 					mylog("possible duplicate notif3, move $dlr to $dlr_dup\n");
					rename($dlr, $dlr_dup);
				#}
				goto END_NOTIF3;
			}
		}
	}
	else
	{
		mylog("!!! msgid is empty in $dlr\n");
		goto END_NOTIF3;
	}

  CREATE_NOTIF3:
	my $pro = $provider;
	$pro =~ s/\d+$//;
	
	#format of DLR from Zain is different from other provider
	#normal: XMS=id:543CD34E00AA33 sub:000 dlvrd:000 submit date:1410140739 done date:1410160739 stat:EXPIRED err:027 text:Abdus invi
	#from Zain: XMS=id:86 submit date:1410160730 done date:1410160730 stat:DELIVRD
	if($pro eq "ZAINSA")
	{
		$sms =~ s/id:(.*) submit/id:$localid submit/;
	}
	else
	{
		$sms =~ s/id:([\w-]+) /id:$localid /;
		#$sms =~ s/id:(.*) sub:/id:$localid sub:/;
	}

	$sms =~ /stat:(.*) err:(.*) text:(.*)/i;
	my $status = $1;
	my $inerr = $2;
	my $inerr2 = $3;

	if($status eq "") #TELNYX's DLR does not contain text
	{
		$sms =~ /stat:([a-zA-Z]+) err:(.*)/;
		$status = $1;
		$inerr = $2;
	}

#	if($pro =~ /VIETTEL/) #For Viettel, new platform does not contain "err" field
#	{
#		$sms =~ /stat:(.*)/i;
#		$status = $1;
#		$inerr = "000";
#	}
#
#	if($pro =~ /SAP/) #for SAP, the err: is always 000, real error code is in Text:
#	{
#		my @err_text = split(/ /,$inerr2);
#		$inerr = $err_text[0];
#	}

	my $status_code;
	my $outerr = $inerr; #external errorcode, record in notif3 table
	$outerr =~ s/\s.*//; # rare case, when text:xxx includes 'text', may get err like "021 text:SMS:"

	if($status eq "DELIVRD")
	{
		$status_code = 2; #ok

	}
	elsif($status eq "UNKNOWN")
	{
		$status_code = 4;
	}
	elsif($status eq "EXPIRED")
	{
		$status_code = 3; #expired
	}
	elsif($status eq "UNDELIV")
	{
		if($pro =~ /^STC/ and ($outerr eq "777" or $outerr eq "999"))
		{
			$status_code = $outerr;
		}
		else
		{
			$status_code = 5; #undeliverable
		}
	}
	elsif($status eq "REJECTD")
	{
		if($pro =~ /^STC/ and ($outerr eq "777" or $outerr eq "999"))
		{
			$status_code = $outerr;
		}
		else
		{
			$status_code = 6; #rejected
		}
	}
	elsif($status eq "DELETED")
	{
		$status_code = 7;
	}
	else
	{
		mylog("UNKNOWN status: $status\n");
		$status_code = 4; #unknown
	}

	#### For message meets retry condition, should not delete the PART info from redis, so here first check if need to retry
	my $keep_id = 0;

	#for longsms, the localid returned to customer may be different than the localid inside in provider's DLR
	my ($clocalid,$customer_subdir) = split(/---/,get_customer_localid($bnumber,$localid,$keep_id) );

	if($status ne "DELIVRD")
	{
		my $key = "$pro:$inerr";

		if($map{$key} ne "")
		{
			my ($newcode,$newstatus) = split(/---/,$map{$key});
			mylog("debug: map $key to $newcode\n");
			$sms =~ s/err:(.*) text:/err:$newcode text:/;
			if($newstatus ne "") #TELESTAX return UNDELIV for EXPIRED
			{
				$sms =~ s/stat:(.*) err:/stat:$newstatus err:/;
			}
			$outerr = $newcode;
		}
	}

	if( ($pro !~ /P2PGW/ and $localid !~ /^[AP]2P/ and $localid !~ /CAMP/) or $pro eq "P2PGW_TPG") #don't check redis for supplier P2PGW_xxx, which is for legacy P2P SMPP customer 
	{
		if($customer_subdir eq "")
		{
			### expired DLR come back after 3 days, redis record already expired ###
			my $epoch_now = time();

			#submit date:2110182305 => convert to 2021-10-18 23:05:00
			my @d = split(//,$submit_date);
			my $year = "20".$d[0].$d[1];
			my $mon = $d[2].$d[3];
			my $day = $d[4].$d[5];
			my $hour = $d[6].$d[7];
			my $min = $d[8].$d[9];
			my $submit_date = "$year-$mon-$day $hour:$min:00";
			my $epoch_submit_date = get_epoch_from_timestamp($submit_date);

			my $delay = $epoch_now - $epoch_submit_date;
			if ($delay > $notif1_redis_expire)
			{
				unlink($dlr); #remove DLR from provider received spool
				mylog("!!! catch pattern: DLR after $delay seconds, REDIS notif1 expired, $bnumber $msgid $localid $status, delete DLR $dlr\n");
				goto END_NOTIF3;
			}
			else
			{
				mylog("!!! catch pattern: REDIS not ready,$dlr,$bnumber $localid, wait for next round\n");
				goto END_NOTIF3;
			}
		}

		if($clocalid ne "" and $clocalid ne "$localid")
		{
			mylog("debug: replace id in DLR XMS to $clocalid\n");
			$sms =~ s/id:(.*) sub:/id:$clocalid sub:/;
		}
	}

	$outerr =~ s/\'/''/g;
	if($provider ne "DUMMY") #amx already inserted into cdr table with notif3_status
	{
		save_sql("insert into notif3 (provider,bnumber,msgid,localid,status,provider_error) values ('$provider','$bnumber','$msgid','$localid',$status_code,'$outerr')", $customer_subdir);
	}

	if($customer_require_dlr{$customer_subdir} eq "1")
	{
		#my $dlr_ts = get_timestamp_from_epoch(time());
		#save_sql("insert into dlr (dbtime,provider,bnumber,mainid,externalid,status,error) values ('$dlr_ts','$provider','$bnumber','$localid','$clocalid',$status_code,'$outerr')");

		if ($localid ne $clocalid)
		{
			my $outputdir = $redis_dir;
			my $tmpoutputdir = "$outputdir/tmp";
			mkdir $outputdir unless -d $outputdir;
			mkdir $tmpoutputdir unless -d $tmpoutputdir;

			my $filename = "$clocalid:$localid:$provider:$status_code:$outerr:$msgid";
			my $outputfile = "$outputdir/$filename";
			my $tmpoutputfile = "$tmpoutputdir/$filename";
			open(my $fh, ">$tmpoutputfile");
			close($fh);
			rename($tmpoutputfile,$outputfile);
			mylog("redis_dlr $outputfile\n");
		}

	}

	#if custoemr require DLR, forward DLR to customer
	if((! -e "$nodlr_dir/$localid") and $customer_require_dlr{$customer_subdir} eq "1")
	{
		#my $notiffile = "$provider---$bnumber---$msgid---$localid";
		my $notiffile = "$provider---$bnumber---$msgid---$clocalid";
		my $outputdir = $customer_notif3_dir{$customer_subdir};
		my $tmpoutputdir = "$outputdir/tmp";
		mkdir $outputdir unless -d $outputdir;
		mkdir $tmpoutputdir unless -d $tmpoutputdir;
		my $outputfile = "$outputdir/$notiffile";
		my $tmpoutputfile = "$tmpoutputdir/$notiffile";
		my %now = get_timestamp();
		if($pro =~ /SKTL/) #GMT+9 South Korea
		{
			my $local_t = $addinfo;
			$local_t =~ s/AddInfo=(\d+).*/$1/;
			my $gmt_t = convert_to_gmt($local_t,9); #South Korea GMT+9
			if($gmt_t ne "-1")
			{
				$sms =~ s/submit date:\d+/submit date:$gmt_t/;
				$sms =~ s/done date:\d+/done date:$gmt_t/;
				$addinfo =~ s/AddInfo=\d+/AddInfo=$gmt_t/;
				$gmt_t .= "00";
				$addinfo =~ s/;\d+/;$gmt_t/;
			}
			else
			{
				mylog("failed to convert to GMT, still use same DLR time\n");
			}
		}

		if( $pro =~ /^STC/ or $pro =~ /^A2PGW/ or $pro =~ /^NOS/ or $pro =~ /^JAVNA/) #convert localtime of provider to GMT
		{
			my $local_t = $addinfo;
			$local_t =~ s/AddInfo=(\d+).*/$1/;

			my $offset = 0;
			if (($pro =~ /^STC/) or ($pro =~ /^A2PGW/))
			{
				$offset = 3;
			}
			elsif($pro =~ /^JAVNA/)
			{
				$offset = 2;
			}
			elsif($pro =~ /^NOS/)
			{
				$offset = 1;
			}

			my $gmt_t = convert_to_gmt($local_t,$offset);
			
			if($gmt_t ne "-1")
			{
				$sms =~ s/submit date:\d+/submit date:$gmt_t/;
				$sms =~ s/done date:\d+/done date:$gmt_t/;
				$addinfo =~ s/AddInfo=\d+/AddInfo=$gmt_t/;
				$gmt_t .= "00";
				$addinfo =~ s/;\d+/;$gmt_t/;
			}
			else
			{
				mylog("failed to convert to GMT, still use same DLR time\n");
			}
		}

		if($tpoa eq "") #if provider return empty TPOA
		{
			$tpoa = "+12340000";
		}

		$bnumber =~ s/^\+//; #remove + from MSISDN
        
		if(open(OUT, ">$tmpoutputfile"))
		{
			print OUT "; encoding=UTF-8\n";
			print OUT "[$customer_subdir]\n";
			print OUT "Created=$now{'created'}\n";
			print OUT "Action=DeliverNotification\n";
			print OUT "Date=$now{'date'}\n";
			print OUT "Phone=$tpoa\n";
			print OUT "OriginatingAddress=$bnumber\n";
			print OUT "MsgId=$clocalid\n";
			print OUT "AddInfo=$addinfo\n";
			print OUT "AddCodes=$addcode\n";
			print OUT "XMS=$sms\n";
			close(OUT);
			rename($tmpoutputfile,$outputfile);

			mylog("create 3rd level notif $outputfile\n\n");
			mylog("; encoding=UTF-8\n");
			mylog("[$customer_subdir]\n");
			mylog("Created=$now{'created'}\n");
			mylog("Action=DeliverNotification\n");
			mylog("Date=$now{'date'}\n");
			mylog("Phone=$tpoa\n");
			mylog("OriginatingAddress=$bnumber\n");
			mylog("MsgId=$clocalid\n");
			mylog("AddInfo=$addinfo\n");
			mylog("AddCodes=$addcode\n");
			mylog("XMS=$sms\n");

			#mylog("\nremove dlr $dlr\n");
			unlink($dlr); #remove DLR from provider received spool

			#if($localidfile ne "")
			if($localidfile ne "" and $provider !~ /EC2NETFORS/) #don't delete localid for ec2netfors
			{
				#mylog("\nremove localidfile $localidfile\n");
				unlink($localidfile);
			}
		}
		else
		{
			mylog("!!! can't create 3rd level notif\n");
		}
	}
	else
	{
		#mylog("$customer_subdir does not require DLR, don't forward DLR\n");
		#mylog("\nremove dlr $dlr\n");
		unlink($dlr);

		if($localidfile ne "")
		{
			mylog("\nremove localidfile $localidfile\n");
			unlink($localidfile);
		}
	}

  END_NOTIF3:
	mylog("=======================================\n");
}

sub convert_to_gmt
{
	my $local_t = shift;
	my $offset = shift;

	#mylog("convert_to_gmt: input: $local_t, $offset\n");

	my @d = split(//,$local_t);
	my $year = "20".$d[0].$d[1];
	my $mon = $d[2].$d[3];
	my $day = $d[4].$d[5];
	my $hour = $d[6].$d[7];
	my $min = $d[8].$d[9];
	
	my $time_orig = "$year-$mon-$day $hour:$min:00";
	my $utime_orig = get_epoch_from_timestamp($time_orig);
	return -1 if $utime_orig eq "-1";
	
	my $utime_new = $utime_orig - $offset*3600;
	my $localtime_new = get_timestamp_from_epoch($utime_new);
	#mylog("new localtime in GMT: $localtime_new\n");
	
	my $time_output = $localtime_new;
	$time_output =~ s/^20//;
	$time_output =~ s/00$//;
	$time_output =~ s/\D//g;
	#mylog("convert_to_gmt: output: $time_output\n");
	return $time_output;
}

sub hash_update
{
	my $db = connectdb();
	mylog("### hash_update...\n");

	mylog("### get %customer_notif3_dir, %customer_require_dlr\n");
	#my $ref = exec_sql($db, "select directory,notif3_dir,notif3 from customers;");
	my $ref = exec_sql($db, "select directory,notif3_dir from customers;");
	while(my @tab = $ref -> fetchrow_array)
	{
		my ($dlr_input, $dlr_output, $require_dlr) = @tab;
		my $subdir = $dlr_input; #/home/amx/sendxms/SERVER_SAP1/received/SERVER_SAP1
		$subdir =~ s/.*\/(.*)/$1/; #SERER_SAP1
		
		if($dlr_output ne "")
		{
			$customer_notif3_dir{$subdir} = "$dlr_output";
		}
		else
		{
			$customer_notif3_dir{$subdir} = "/home/amx/sendxms/$subdir/spool/$subdir";
		}
	}
	$ref -> finish;

	mylog("### get %smsc_name_id, %h_smsc_provider\n");
	$ref = exec_sql($db, "select id,name,provider from smsc;");
	while(my ($id,$name,$providerid) = $ref -> fetchrow_array)
	{
		$smsc_name_id{$name} = $id;
		$h_smsc_provider{$id} = $providerid;
	}
	$ref -> finish;

	#debug
	#while(my ($key, $value) = each %customer_notif3_dir)
	#{
	#	mylog("debug: $key => $value, $customer_require_dlr{$key}\n");
	#}

	### don't check received dir for provider live=0
	mylog("### don't check received directory for provider live=0:\n");
	$ref = exec_sql($db, "select smsc.id as smsc_id,smsc.name as smsc_name,directory,provider.name as provider_name from smsc join provider on provider.id=smsc.provider where provider in (select id from provider where live=0) order by smsc_name;");
	while(my ($smsc_id,$smsc_name,$dir,$provider_name) = $ref -> fetchrow_array)
	{
		my $subdir = $dir; #/home/amx/sendxms/1TOALL/spool/1TOALL
		$subdir =~ s/.*\/(.*)/$1/; #1TOALL
		$nocheckdir{$subdir} = 1;
		mylog(" - $subdir\n");
	}
	$ref -> finish;

	$db -> disconnect();

	#error mapping
	mylog("### check error mapping\n");
	%map = ();
	open(CFG, "<$cfg") or die "Can not open $cfg: $!\n";
	while(my $line = <CFG>)
	{
		chomp($line);
		next if $line =~ /^#/;
		my ($key,$code,$status) = split(/,/,$line);
		$map{$key} = "$code---$status";
		mylog("- $key -> $code---$status\n");
	}
	close(CFG);

	open(RETRYCFG, "<$retry_cfg") or die "can't open $retry_cfg: $!\n";
	while(my $line = <RETRYCFG>)
	{	
		chomp($line);
		next if $line =~ /^#/;

		$retry_to = $line;
		$retry_to_tmp = "$retry_to/tmp";
	}
	close(RETRYCFG);
	mylog("### get retry_to dir: $retry_to\n");

}

if(open(LOCK, "<$lock"))
{
	my $pid = <LOCK>;
	close(LOCK);
	while(kill(0,$pid))
	{
		mylog("notif3gen $pid is running, will kill it and run my own\n");
		kill(15, $pid);
		sleep(5);
	}
}

open(LOCK, ">$lock") or die "can't write in $lock: $!\n";
print LOCK $$;
close(LOCK);

mylog("********************************************************\n");
mylog("Start notif3gen\n");
mylog("********************************************************\n");

#get output dir to forward DLR to customer
mylog("### get output DLR spool for customers\n"); 
hash_update();

#my $max_load = get_max_load();
#$max_load = 10 if $max_load eq "";

while(1)
{
	my $count = 0;
	my $i = 0;

	if(opendir(SENDXMS, "$sendxmsdir"))
	{
		while(my $providerdir = readdir(SENDXMS))
		{
			if( -d "$sendxmsdir/$providerdir" and $providerdir ne "." and $providerdir ne "..")
			{
				if(local_continue($providerdir) == 1)
				{
					my $s_pid = fork;

					my $time = time() + 300;
					if($s_pid == 0)
					{
						while(1)
						{
 							my $current_time = time();
							if( $current_time > $time)
							{
								mylog("fork $current_time > $time");
								exit;
							}
							exit if $mark_terminate == 1;

							checkdir("$sendxmsdir/$providerdir/received/$providerdir",$providerdir);
							select(undef,undef,undef,1);
					    }
					}
					if($s_pid > 0)
					{
						$i++;
						mylog("($$) $i forks $providerdir");
						$pid{$s_pid} = 1;
					}
					if($s_pid < 0)
					{
						mylog("Can not fork for $providerdir: $!");
					}
					#select(undef,undef,undef,0.03);
				}
				else
				{
					mylog("!!! ignore $providerdir\n");
				}
			}
		}
		close(SENDXMS);
		while(1)
		{
			my $w_pid = waitpid(-1, WNOHANG);
			sleep(1) if $w_pid == 0;
			last if $w_pid == -1;
			delete($pid{$w_pid});
			$i--;
			mylog("($$) $i forks still alive");
			check_signal();
		}
		%pid = ();
	}
	else
	{
		mylog("!!!can't open $sendxmsdir: $!\n");
	}
	check_signal();
}

#$status_code = 2; #ok
#$status_code = 4; #UNKNOWN
#$status_code = 3; #EXPIRED
#$status_code = 5; #UNDELIV
#$status_code = 6; #REJECTD
#$status_code = 7; #DELETED
