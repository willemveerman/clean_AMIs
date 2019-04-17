import argparse
import os
import shutil
from datetime import datetime as dt
import textwrap
import getpass

parser = argparse.ArgumentParser(description=
                                 """
                                 Deletes old AMIs and their associated snapshots:

                                 if there are more than three AMIs for a particular instance type,
                                 all but the youngest two are deleted.

                                 AMIs that are the base image of non-terminated EC2 instances are excluded from deletion.

                                 This script creates a virtualenv in /tmp/cleanup_venv/, 
                                 installs all necessary dependencies and runs code within the virtualenv.

                                 Upon completion /tmp/cleanup_venv/ is deleted.

                                 The script will first display the AMIs and snapshots earmarked for deletion and then ask
                                 the user if he or she wants to proceed. The -i option will simply list qualifying AMIs and then exit.
                                 """,
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)

parser.add_argument('-v', '--verbose',
                    action="store_true",
                    help='display verbose output for debugging')

parser.add_argument('-i', '--info',
                    action="store_true",
                    help='return qualifying AMIs but do not delete')

parser.add_argument('-p', '--profile',
                    help='AWS credentials profile',
                    required=True)

parser.add_argument('-r', '--region',
                    default='eu-west-2',
                    help='AWS region')

args = parser.parse_args()

print('{0} - AMI CLEANUP - INFO - LOADING VIRTUAL ENV'.format(str(dt.now().strftime("%Y-%m-%d %H:%M:%S"))))

if os.system('pip show virtualenv >/dev/null 2>&1') != 0:
    os.system('python -m pip install virtualenv --target=/tmp/bin/ >/dev/null 2>&1')

# create a new venv in the temp directory - it will be deleted once the script has finished
path = '/tmp/cleanup_venv/'
if os.path.isdir(path):
    shutil.rmtree(path)

os.system('virtualenv -p /usr/bin/python2.7 ' + path + 'cleanupenv --no-setuptools --no-wheel >/dev/null')

# install dependencies which aren't in the standard lib, boto3 in this case
os.system(path + 'cleanupenv/bin/pip install boto3 >/dev/null')

imp = 'import boto3 as b; from datetime import datetime as dt; import json; '

session_string = """
sess = b.Session(profile_name='{0}', region_name='{1}'); """.format(args.profile, args.region)

ec2_string = "ec2 = sess.resource('ec2'); "

query_string = """

object_map = {}

for ami in ec2.images.all().filter(Owners=['self']):

    try:
        ami.reload()
    except Exception:
        continue

    if getattr(ami, 'state', 0) == 'available':

        instance_type = ami.name[0:ami.name.find('_')]  # substring strip timestamp from AMI name

        object_map.setdefault(instance_type, []).append(
            dict(name = ami.name,
                 cdateobj = dt.strptime(ami.creation_date, '%Y-%m-%dT%H:%M:%S.000Z'),
                 id = ami.id,
                 snapshots = [i['Ebs'].get('SnapshotId') for i in ami.block_device_mappings]))

amis_for_deletion = []

snaps_for_deletion = []

info_string = ''

amis_in_deployment = [s.image_id for s in ec2.instances.all() if s.state['Name'] != 'terminated']

for key, value in object_map.iteritems():
    if len(value) > 3:
        for image in sorted(value, key=lambda k: k['cdateobj'])[0:-2]: # all but youngest two AMIs

            if image['id'] not in amis_in_deployment:

                info_string += 'STAGED FOR DELETION: '+ image['id'] + ' ' + image['name'] + ' ' + str(image['snapshots'])+'\\n'
                amis_for_deletion.append((image['id'], image['name']))
                snaps_for_deletion.extend(image['snapshots'])

if amis_for_deletion:
    with open ('/tmp/cleanup_venv/cleanupenv/amis.json', 'w') as j:
        j.write(json.dumps(amis_for_deletion, ensure_ascii=False))
    with open ('/tmp/cleanup_venv/cleanupenv/info.txt', 'w') as t:
        t.write(info_string)

if snaps_for_deletion:
    with open ('/tmp/cleanup_venv/cleanupenv/snaps.json', 'w') as j:
        j.write(json.dumps(snaps_for_deletion, ensure_ascii=False))
"""

print("{0} - AMI CLEANUP - INFO - CHECKING FOR REDUNDANT AMIS".format(dt.now().strftime("%Y-%m-%d %H:%M:%S")))

os.system('/tmp/cleanup_venv/cleanupenv/bin/python2.7 -c "{0} {1} {2} {3}"'.format(imp,
                                                                                   session_string,
                                                                                   ec2_string,
                                                                                   query_string))

if not os.path.isfile('/tmp/cleanup_venv/cleanupenv/amis.json'):
    print("{0} - AMI CLEANUP - INFO - NO AMIS TO DELETE".format(dt.now().strftime("%Y-%m-%d %H:%M:%S")))
else:
    with open('/tmp/cleanup_venv/cleanupenv/info.txt', 'r') as info:
        info_string = info.read()
        print(info_string)

    if not args.info:

        print("Proceed to deletion?")
        print("Enter 'yes' or 'y' - any other input will halt and exit")
        print("Answer: ")
        response = input()

        if response == 'y' or response == 'yes':

            imp_exec = 'import boto3 as b; import json; import logging; '

            log_string = textwrap.dedent("""

            logger = logging.getLogger('AMI CLEANUP')

            logger.setLevel(logging.INFO)

            log = logging.StreamHandler()

            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s','%Y-%m-%d %H:%M:%S')

            log.setFormatter(formatter)

            logger.addHandler(log)

            """)

            load_data = textwrap.dedent("""

            with open('/tmp/cleanup_venv/cleanupenv/amis.json', 'r') as amis:
                amis_for_deletion = json.load(amis)
            with open('/tmp/cleanup_venv/cleanupenv/snaps.json', 'r') as snaps:
                snaps_for_deletion = json.load(snaps)

            """)

            exec_string = textwrap.dedent("""

            exception_msg = False

            debug_message = ''

            for ami in amis_for_deletion:
                ami_obj = ec2.Image(ami[0])
                logger.debug('ATTEMPTING DELETION OF ' + ami[0] + ' - ' + ami[1])
                try:
                    logger.debug(ami_obj.deregister())
                except Exception as exception_msg:
                    logger.debug('ERROR ON AMI: ' + ami[0] + ' - ' + ami[1]+': '+str(exception_msg))

            for snapshot in snaps_for_deletion:
                snapshot_obj = ec2.Snapshot(snapshot)
                logger.debug('ATTEMPTING DELETION OF ' + snapshot)
                try:
                    logger.debug(snapshot_obj.delete())
                except Exception as exception_msg:
                    logger.debug('ERROR ON SNAPSHOT: ' + snapshot + ': '+str(exception_msg))

            if exception_msg and logger.getEffectiveLevel() == 20:
                    logger.info('ERROR ENCOUNTERED - FOR DETAILS, RETRY WITH VERBOSE OPTION')
            elif exception_msg:
                    logger.debug('ERROR ENCOUNTERED')
            else:
                logger.info('ALL OBJECTS SUCCESSFULLY DELETED')
            """)

            if args.verbose:
                log_string += """logger.setLevel(logging.DEBUG)"""

            os.system('/tmp/cleanup_venv/cleanupenv/bin/python2.7 -c "{0} {1} {2} {3} {4} {5}"'.format(imp_exec,
                                                                                                       log_string,
                                                                                                       session_string,
                                                                                                       ec2_string,
                                                                                                       load_data,
                                                                                                       exec_string))

shutil.rmtree('/tmp/cleanup_venv/')
