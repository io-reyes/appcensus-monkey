import sdk
import configparser
import argparse
import os
import sys
import datetime
import time
import subprocess
import tarfile
from glob import glob
from dbops import dbops

def parse_config(config_file):
    assert os.path.isfile(config_file), '%s is not a valid file or path to file' % config_file

    config = configparser.ConfigParser()
    config.read(config_file)

    assert 'monkey' in config.sections(), 'Config file %s does not contain a monkey section' % config_file
    assert 'TimeLimitMins' in config['monkey'], 'Config file %s does have a TimeLimitMins value in the monkey section' % config_file
    assert 'InitialScreenSecs' in config['monkey'], 'Config file %s does have an InitialScreenSecs value in the monkey section' % config_file
    assert 'RebootAfterRun' in config['monkey'], 'Config file %s does have an RebootAfterRun value in the monkey section' % config_file

    time_limit_mins = config['monkey'].getint('TimeLimitMins')
    initial_screen_secs = config['monkey'].getint('InitialScreenSecs')
    reboot_after_run = config['monkey'].getboolean('RebootAfterRun')
    #allow_hardware_keys = config['monkey']['AllowHardwareKeys'] if 'AllowHardwareKeys' in config['monkey'] else False

    return (time_limit_mins, initial_screen_secs, reboot_after_run)

def parse_args():
    parser = argparse.ArgumentParser(description='Automatic monkey test script')
    parser.add_argument('config', help='Path to sdk.config file')
    parser.add_argument('outdir', help='Directory where results will be stored. A subdirectory outdir/<package>/<versioncode>/ will be made if necessary')
    parser.add_argument('--dbcreds', '-D', help='Database credentials file, used to retrieve apps from DB')
    parser.add_argument('--apkroot', help='Path to APK root directory, required if dbcreds is specified')
    parser.add_argument('--apk', '-a', help='Path to APK file to test (overrides --dbcreds)')
    parser.add_argument('--skip-install', '-s', action='store_true', help='Skips the installation step. Use this if the app is already installed on the device (i.e., for paid apps).')
    parser.add_argument('--device', '-d', help='Android device ID, if multiple devices are connected')
    parser.add_argument('--mincharge', '-c', type=int, default=5, help='Required minimum charge to proceed, default 5')
    parser.add_argument('--test', action='store_true', help='Perform the debug test function instead of actually doing a monkey run')

    return parser.parse_args()

def monkey(config, apk, outdir, print_to_file=True, skip_install=False, compress_screens=True):
    (time_limit_mins, initial_screen_secs, reboot_after_run) = parse_config(config)

    # Create the output directory outdir/<package>/<versioncode>/test-<utc YYYYmmddHHMMSS>/
    package = sdk.aapt_package(apk)
    version_code = sdk.aapt_version_code(apk)
    data_dir = os.path.join(outdir, package, version_code)
    if(not os.path.isdir(data_dir)):
        os.makedirs(data_dir)

    test_time = datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')
    data_dir = os.path.join(data_dir, 'test-%s' % test_time)
    assert not os.path.exists(data_dir), 'Data output directory %s already exists' % data_dir
    os.makedirs(data_dir)

    # Redirect print statements to file
    orig_sysout = sys.stdout
    log_file = os.path.join(data_dir, '%s-%s-test-%s.log' % (package, version_code, test_time))
    f = None
    if print_to_file:
        f = open(log_file, 'w')
        sys.stdout = f

    # Show declared permissions
    print(sdk.aapt_permissions(apk))

    # Clear the logs
    sdk.adb_clear_logs()

    # Grab the device file
    sdk.adb_get_dev_file(os.path.join(data_dir, '%s-%s-test-%s.device' % (package, version_code, test_time)))

    # Install the app
    if not skip_install:
        sdk.adb_install(apk)
    assert sdk.adb_package_installed(package), '%s was not installed successfully' % package

    # Clear the screen
    sdk.adb_clear_screen()

    # Start Lumen
    sdk.adb_start_lumen()

    ###########################################################################################
    # PUTTING THIS WHOLE THING UNDER A TRY BLOCK BECAUSE THE APP COULD CRASH THE WHOLE SYSTEM #
    ###########################################################################################
    try:
        # Start the app and take screenshots of the initial load
        sdk.adb_start_app(package)
        end_time = datetime.datetime.now() + datetime.timedelta(seconds=initial_screen_secs)
        screen_count = 0
        while(datetime.datetime.now() < end_time):
            screen_count = screen_count + 1
            screen_file = os.path.join(data_dir, '%s-%s-test-%s-start-%d.png' % (package, version_code, test_time, screen_count))
            sdk.adb_screenshot(screen_file)

        # Explore the app
        end_time = datetime.datetime.now() + datetime.timedelta(minutes=time_limit_mins)
        screen_count = 0
        while(datetime.datetime.now() < end_time):
            sdk.adb_lumen_check()
            screen_count = screen_count + 1
            sdk.adb_monkey(package)
            screen_file = os.path.join(data_dir, '%s-%s-test-%s-run-%d.png' % (package, version_code, test_time, screen_count))
            sdk.adb_screenshot(screen_file)

        # Uninstall the app and wait for logs to be flushed
        if not skip_install:
            sdk.adb_uninstall_all()
            time.sleep(5)

        # Toggle Lumen a couple times to flush its logs
        sdk.adb_toggle_lumen()
        sdk.adb_toggle_lumen()
        sdk.adb_toggle_lumen()
        sdk.adb_stop_lumen()

        # Compress screenshots
        if(compress_screens):
            _compress_pngs(data_dir, '%s-%s-test-%s-screens.tar.bz2' % (package, version_code, test_time))

        sdk.log('SUCCESS', package)

    except subprocess.CalledProcessError as e:
        sdk.log('CRASH', str(e))

    finally:
        # Dump logcat and dmesg
        sdk.adb_show_logs()

        # Close the log file and restore stdout
        if f is not None:
            sys.stdout = orig_sysout
            f.close()

def _compress_pngs(png_dir, outfile, delete_pngs=True):
    # Find all PNGs in the directory and compress them as <png_dir>/<outfile>.tar.bz2
    png_files = [os.path.join(png_dir, x) for x in os.listdir(png_dir) if x.endswith('.png')]

    if len(png_files) > 0:
        tar = tarfile.open(os.path.join(png_dir, outfile), 'w:bz2')
        for png in png_files:
            tar.add(png, arcname=os.path.basename(png))
        tar.close()

    if(delete_pngs):
        for png in png_files:
            os.remove(png)

def _check_charge(mincharge, charge_to=90):
    # Let the device charge up to a certain level once it drops below the minimum charge level
    charge = sdk.adb_battery_level()
    if(charge < mincharge):
        sdk.adb_screen_turn_off()
        while(charge < charge_to):
            print('Battery charge at %d, waiting until at least %d' % (charge, charge_to))
            time.sleep(10 * 60)
            charge = sdk.adb_battery_level()
        sdk.adb_screen_turn_on()

def _pre_run_checks(mincharge, uninstall_all=True):
    # Reboot and wait until the device is ready before proceeding
    sdk.adb_reboot(wait=True)

    # Ensure a minimum charge level
    _check_charge(args.mincharge)

    # Clear out all non-standard apps
    if(uninstall_all):
        sdk.adb_uninstall_all()

def _db_init(dbcreds):
    config = configparser.ConfigParser()
    config.read(dbcreds)

    assert 'Database' in config.sections(), 'Credentials file %s does not contain a Database section' % dbcreds
    assert 'host' in config['Database'], 'Credentials file %s does not have a host value in the Database section' % dbcreds
    assert 'database' in config['Database'], 'Credentials file %s does not have a database value in the Database section' % dbcreds
    assert 'user' in config['Database'], 'Credentials file %s does not have a user value in the Database section' % dbcreds
    assert 'password' in config['Database'], 'Credentials file %s does not have a password value in the Database section' % dbcreds

    creds = config['Database']
    dbops.init(creds['host'], creds['database'], creds['user'], creds['password'])

def _db_run(config, dbcreds, apk_root, outdir):
    assert dbcreds is not None and os.path.isfile(dbcreds), 'A valid database secrets file must be supplied'
    assert apk_root is not None and os.path.isdir(apk_root), 'A valid APK root directory must be supplied'

    # Get a package name and version code from the database
    _db_init(dbcreds)
    (package_name, version_code) = dbops.get_app_to_test()

    # Get the APK
    apk = os.path.join(apk_root, package_name, str(version_code), '%s-%d.apk' % (package_name, version_code))

    if(os.path.isfile(apk)):
        # Run the test, mark the database appropriately
        dbops.update_app_run_status(package_name, 1)

        try:
            monkey(config, apk, outdir, print_to_file=True)
            dbops.update_app_run_status(package_name, 2)
        except Exception as e:
            print(str(e))
            dbops.update_app_run_status(package_name, -1)

    else:
        dbops.update_app_run_status(package_name, -1)
        assert os.path.isfile(apk), '%s is not a valid APK path' % apk

def _debug_test():
    # Test SDK calls
    #sdk.adb_clear_logs()
    #sdk.adb_show_logs()
    _compress_pngs('/home/ioreyes/coppa/mounts/run-data/com.aetnent.history.android.kids.PortaPilots/47245/test-20171026194243/', 'com.aetnent.history.android.kids.PortaPilots-47245-test-20171026194243-screens.tar.bz2')

if __name__ == '__main__':
    args = parse_args()
    config = args.config
    outdir = args.outdir

    dev = args.device
    sdk.init(config, device=dev)

    if(args.test):
        _debug_test()
    else:
        if(args.apk is not None):
            apk = args.apk
            assert os.path.isfile(apk), '%s is not a valid APK path' % apk
            skip_install = args.skip_install

            _pre_run_checks(args.mincharge, uninstall_all=not skip_install)

            monkey(config, apk, outdir, print_to_file=True, skip_install=skip_install)
        else:
            _pre_run_checks(args.mincharge)

            _db_run(config, args.dbcreds, args.apkroot, outdir)


