import configparser
import os
import subprocess
import io
import random
import multiprocessing
import sys
import shutil

import time
from datetime import datetime,timedelta

adb = None
aapt = None

device_serial = None

lumen_pkg = 'edu.berkeley.icsi.haystack'
devfilegen_pkg = 'edu.berkeley.icsi.devfilegen'

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def _parse_config(config_file):
    assert os.path.isfile(config_file), '%s is not a valid file or path to file' % config_file

    config = configparser.ConfigParser()
    config.read(config_file)

    assert 'sdk' in config.sections(), 'Config file %s does not contain an sdk section' % config_file
    assert 'ADBPath' in config['sdk'], 'Config file %s does not have an ADBPath value in the sdk section' % config_file
    assert 'AAPTPath' in config['sdk'], 'Config file %s does not have an AAPTPath value in the sdk seciton' % config_file

    adb_path = config['sdk']['ADBPath']
    aapt_path = config['sdk']['AAPTPath']

    assert os.path.isfile(adb_path), 'adb binary not found in %s' % adb_path
    assert os.path.isfile(aapt_path), 'aapt binary not found in %s' % aapt_path

    global adb, aapt
    adb = adb_path
    aapt = aapt_path

def init(config_file, device=None):
    _parse_config(config_file)

    global device_serial
    if device is not None and len(device) > 0:
        device_serial = device
    else:
        (success, device_serial) = adb_shell('getprop ro.serialno')
        assert success, 'Unable to get device serial number through adb getprop ro.serialno'
    device_serial = device_serial.lower().strip()

def log(tag, message):
    utc_time = datetime.utcnow()
    utc_str = utc_time.strftime('%Y-%m-%d-%H:%M:%S')

    print('(%s) %s -- %s' % (tag, utc_str, message))

def elog(tag, message):
    utc_time = datetime.utcnow()
    utc_str = utc_time.strftime('%Y-%m-%d-%H:%M:%S')

    eprint('(%s) %s -- %s' % (tag, utc_str, message))

##################
## ADB WRAPPERS ##
##################
def adb_call(command, *args, ret_queue=None):
    global adb, device_serial

    assert adb is not None, 'SDK configuration not yet initialized, need to init() first'
    adb_cmd = [adb, '-s', device_serial, command] if device_serial is not None else [adb, command]
    adb_cmd.extend(args)
    elog('ADB', str(adb_cmd))

    try:
        result = subprocess.check_output(adb_cmd, stderr=subprocess.STDOUT).decode('UTF-8', 'backslashreplace')
    except Exception as e:
        eprint(str(e))
        result = None

    if(ret_queue is not None):
        ret_queue.put(result)

    return result 

def adb_call_timeout(command, *args, timeout_secs=90, quit_on_fail=False):
    ret = multiprocessing.Queue()
    proc = multiprocessing.Process(target=adb_call, args=(command, *args), kwargs={'ret_queue':ret})
    elog('ADB', 'Starting command "%s" with timeout %d' % (command, timeout_secs))
    proc.start()

    end_time = datetime.now() + timedelta(seconds=timeout_secs)
    success = True
    while proc.is_alive():
        time.sleep(2)
        if(datetime.now() > end_time):
            elog('ADB', 'Command "%s" timed out' % command)

            proc.terminate()
            proc.join()

            success = False

    elog('ADB', 'Command "%s" terminated' % command)

    if(not success and quit_on_fail):
        log('CRASH', 'Failed on command "%s", rebooting' % command)
        sys.exit(1)

    if success:
        return (success, ret.get_nowait())
    else:
        return (success, None)

def adb_shell(*args, timeout_secs=10, retry_limit=5):
    (success, ret) = adb_call_timeout('shell', *args, timeout_secs=timeout_secs)

    while not success and retry_limit > 0:
        (success, ret) = adb_call_timeout('shell', *args, timeout_secs=timeout_secs)
        retry_limit = retry_limit - 1

    return (success, ret)

def adb_shutdown():
    adb_shell('reboot -p')

def adb_wait_boot(timeout_secs=240):
    end_time = datetime.now() + timedelta(seconds=timeout_secs)

    log('WAITBOOT', 'Checking if device is booted')

    while(not (adb_isconnected() and adb_isbooted())):
        # Re-issue the reboot command if it's taking too long
        if(datetime.now() > end_time and adb_isconnected()):
            log('REBOOT', 'Retrying reboot after taking longer than %d seconds' % timeout_secs)
            adb_shell('reboot')
            end_time = datetime.now() + timedelta(seconds=timeout_secs)

        time.sleep(2)

    log('WAITBOOT', 'Device is booted')

def adb_reboot(wait=False):
    log('REBOOT', 'Reboot device')
    adb_shell('reboot')

    if(wait):
        adb_wait_boot()

def adb_isconnected():
    global device_serial
    result = adb_call('devices')
    device_found = result.lower().find(device_serial) >= 0

    return device_found

def adb_isbooted():
    (success, result) = adb_shell('getprop sys.boot_completed')

    return success and result.strip() == '1'

def adb_install(apk_file, grant_all_perms=True):
    assert os.path.isfile(apk_file), '%s is not a valid APK path'

    log('INSTALL', 'Calling aapt on %s' % apk_file)
    package = aapt_package(apk_file)
    log('INSTALL', 'Installing %s' % package)
    adb_call_timeout('install', '-r', apk_file, timeout_secs=120)

    if grant_all_perms:
        log('INSTALL', 'Granting all permissions')
        permissions = aapt_permissions(apk_file)
        for perm in permissions:
            try:
                adb_shell('pm grant %s %s' % (package, perm))
            except subprocess.CalledProcessError as e:
                # Ignore error raised by trying to turn on non-toggleable permissions
                print(e.output.decode('UTF-8', 'backslashreplace'))
                continue

dont_uninstall = set(['android', \
    'com.android.apps.tag', \
    'com.android.backupconfirm', \
    'com.android.bluetooth', \
    'com.android.bluetoothmidiservice', \
    'com.android.bookmarkprovider', \
    'com.android.calculator2', \
    'com.android.calendar', \
    'com.android.calllogbackup', \
    'com.android.camera2', \
    'com.android.captiveportallogin', \
    'com.android.carrierconfig', \
    'com.android.certinstaller', \
    'com.android.contacts', \
    'com.android.defcontainer', \
    'com.android.deskclock', \
    'com.android.dialer', \
    'com.android.documentsui', \
    'com.android.dreams.basic', \
    'com.android.dreams.phototable', \
    'com.android.email', \
    'com.android.externalstorage', \
    'com.android.gallery3d', \
    'com.android.hotwordenrollment', \
    'com.android.htmlviewer', \
    'com.android.inputdevices', \
    'com.android.inputmethod.latin', \
    'com.android.keychain', \
    'com.android.launcher3', \
    'com.android.location.fused', \
    'com.android.managedprovisioning', \
    'com.android.messaging', \
    'com.android.mms.service', \
    'com.android.music', \
    'com.android.musicfx', \
    'com.android.nfc', \
    'com.android.omadm.service', \
    'com.android.onetimeinitializer', \
    'com.android.pacprocessor', \
    'com.android.phone', \
    'com.android.printspooler', \
    'com.android.providers.calendar', \
    'com.android.providers.contacts', \
    'com.android.providers.downloads', \
    'com.android.providers.downloads.ui', \
    'com.android.providers.media', \
    'com.android.providers.settings', \
    'com.android.providers.telephony', \
    'com.android.providers.userdictionary', \
    'com.android.proxyhandler', \
    'com.android.quicksearchbox', \
    'com.android.sdm.plugins.connmo', \
    'com.android.sdm.plugins.dcmo', \
    'com.android.sdm.plugins.diagmon', \
    'com.android.sdm.plugins.sprintdm', \
    'com.android.server.telecom', \
    'com.android.settings', \
    'com.android.sharedstoragebackup', \
    'com.android.shell', \
    'com.android.smspush', \
    'com.android.statementservice', \
    'com.android.systemui', \
    'com.android.vending', \
    'com.android.vpndialogs', \
    'com.android.wallpaper.livepicker', \
    'com.android.wallpapercropper', \
    'com.android.webview', \
    'com.google.android.backuptransport', \
    'com.google.android.feedback', \
    'com.google.android.gms', \
    'com.google.android.gsf', \
    'com.google.android.gsf.login', \
    'com.google.android.instantapps.supervisor', \
    'com.google.android.launcher.layouts.bullhead', \
    'com.google.android.onetimeinitializer', \
    'com.google.android.packageinstaller', \
    'com.google.android.partnersetup', \
    'com.google.android.play.games', \
    'com.google.android.setupwizard', \
    'com.google.android.syncadapters.calendar', \
    'com.google.android.syncadapters.contacts', \
    'com.google.android.tts', \
    'com.lexa.fakegps', \
    'com.lge.HiddenMenu', \
    'com.lge.lifetimer', \
    'com.qualcomm.atfwd', \
    'com.qualcomm.qcrilmsgtunnel', \
    'com.qualcomm.qti.rcsbootstraputil', \
    'com.qualcomm.qti.rcsimsbootstraputil', \
    'com.qualcomm.timeservice', \
    'com.quicinc.cne.CNEService', \
    'com.svox.pico', \
    'com.verizon.omadm', \
    'edu.berkeley.icsi.devfilegen', \
    'edu.berkeley.icsi.haystack', \
    'jp.co.omronsoft.openwnn', \
    'org.chromium.webview_shell'])

def adb_uninstall_all():
    global dont_uninstall

    (success, packages) = adb_shell('pm list packages')
    if(success):
        installed = set([x.replace('package:', '') for x in packages.split('\n') if x is not None and len(x) > 0])
        to_uninstall = installed - dont_uninstall

        for package in to_uninstall:
            log('UNINSTALL', 'Uninstalling %s' % package)
            adb_call_timeout('uninstall', package)

def adb_start_app(package):
    # Always start from the home screen
    adb_shell('input keyevent 3')
    time.sleep(2)

    adb_shell('monkey -p %s -c android.intent.category.LAUNCHER 1' % package)

def adb_package_installed(package_name):
    (success, output) = adb_shell('pm list packages %s' % package_name)
    return success and len(output) > 0

def adb_toggle_lumen(clear_db=False):
    global lumen_pkg
    assert adb_package_installed(lumen_pkg) > 0, 'Lumen not installed on device'

    adb_start_app(lumen_pkg)
    time.sleep(2)

    if(clear_db):
        adb_shell('input tap 1000 100')     # Click on the three dots
        time.sleep(2)
        adb_shell('input tap 700 250')      # Click on Database
        time.sleep(2)
        adb_shell('input tap 500 600')      # Click on Wipe All Data
        time.sleep(2)
        adb_shell('input tap 900 1000')     # Click on confirm
        time.sleep(2)
        adb_shell('input keyevent 4')       # Go back
        time.sleep(2)

    adb_shell('input tap 500 750')  # TODO Make this more robust across devices
    time.sleep(2)
    adb_shell('input keyevent 3')
    time.sleep(2)

def adb_start_lumen():
    global lumen_pkg
    assert adb_package_installed(lumen_pkg) > 0, 'Lumen not installed on device'

    adb_stop_lumen()
    adb_shell('svc wifi enable')    # Ensure wi-fi is on before turning on Lumen
    adb_toggle_lumen(clear_db=True)

def adb_stop_lumen():
    # TODO Find a way to reliably stop lumen
    global lumen_pkg
    adb_shell('am stopservice -n %s/.services.LocalVpnService' % lumen_pkg)
    adb_shell('am force-stop %s' % lumen_pkg)

def adb_get_dev_file(save_as):
    global devfilegen_pkg
    assert adb_package_installed(lumen_pkg) > 0, 'Lumen not installed on device'

    log('DEVFILE', 'Saving device file as %s' % save_as)
    serial = adb_call('get-serialno').strip()
    adb_start_app(devfilegen_pkg)
    time.sleep(2)
    adb_shell('input keyevent 3')
    adb_call_timeout('pull', '/sdcard/%s.device' % serial, timeout_secs=10)

    shutil.move('%s.device' % serial, save_as)

def adb_clear_logs():
    log('LOGS', 'Clearing dmesg and logcat')
    adb_shell('su 0 dmesg -c')
    adb_shell('su 0 rm /data/data/com.android.launcher3/__ucb_fs_log__')
    adb_shell('su 0 rm /sdcard/lumen_*.log')

    # Workaround to logcat not clearing: just keep trying
    for n in range(5):
        adb_call_timeout('logcat', '-c', timeout_secs=10)

def adb_clear_screen():
    # Just bang on the "enter" button from the Home Screen a bunch of times
    adb_shell('input keyevent 3')
    time.sleep(2)

    for n in range(10):
        adb_shell('input keyevent 66')
        time.sleep(1)

    adb_shell('input keyevent 3')
    time.sleep(2)

def adb_show_logs():
    log('LOGS', '-----logcat start-----')
    print(adb_call('logcat', '-d'))
    log('LOGS', '-----logcat end-----')

    log('LOGS', '-----dmesg start-----')
    print(adb_shell('su 0 dmesg', retry_limit=0))
    log('LOGS', '-----dmesg end-----')

def adb_is_wifi_connected(enable_wifi=True):
    if(enable_wifi):
        adb_shell('svc wifi enable')    # Ensure wi-fi is on before checking
        time.sleep(20)

    (success, result) = adb_shell("dumpsys wifi | grep 'mNetworkInfo' | cut -d ',' -f2 | cut -d '/' -f2")
    return success and result.strip() == 'CONNECTED'

def adb_is_screen_on():
    (success, result) = adb_shell("dumpsys power | grep 'Display Power' | cut -d'=' -f2")
    return success and result.strip() == 'ON'

def adb_screen_turn_on():
    if not adb_is_screen_on():
        adb_shell('input keyevent 26')

def adb_screen_turn_off():
    if adb_is_screen_on():
        adb_shell('input keyevent 26')

def adb_screenshot(out_file):
    log('SCREENSHOT', 'Screenshot %s' % out_file)
    screen_tmp = '/sdcard/coppa-screen.png.tmp'

    (success, ret) = adb_shell('screencap -p %s' % screen_tmp)
    if(success):
        adb_call_timeout('pull', screen_tmp, out_file, timeout_secs=10)
        adb_shell('rm %s' % screen_tmp)

def adb_is_portrait():
    (success, result) = adb_shell('dumpsys input | grep SurfaceOrientation')
    return success and result.strip().endswith('0')

def adb_lumen_check():
    # First ensure that the device is booted up
    adb_wait_boot()

    # Then check if the VPN tunnel is available, start Lumen if it's not available
    (success, output) = adb_shell('ifconfig | grep tun0 | wc -l')
    if(success and int(output) == 0):
        log('LUMEN', 'Lumen VPN tunnel not active, turning on now')
        adb_start_lumen()

def adb_monkey(package, seed=None, delay_ms=1000, event_count=100, pct_trackball=0, pct_nav=0, pct_majornav=0, pct_syskeys=0, pct_flip=0, pct_anyevent=0):
    seed = seed if seed is not None else random.randrange(999999999999)

    log('MONKEY', 'Seed=%d' % seed)
    log('MONKEY', 'DelayMS=%d' % delay_ms)
    log('MONKEY', 'EventCount=%d' % event_count)

    monkey_args = 'monkey \
                   -s %d \
                   -p %s \
                   --throttle %s \
                   --pct-trackball %d \
                   --pct-nav %d \
                   --pct-majornav %d \
                   --pct-syskeys %d \
                   --pct-flip %d \
                   --pct-anyevent %d  \
                   --ignore-crashes --ignore-timeouts --ignore-security-exceptions -v %d' % \
                   (seed, package, delay_ms, pct_trackball, pct_nav, pct_majornav, pct_syskeys, pct_flip, pct_anyevent, event_count)
    adb_shell(monkey_args, timeout_secs=120, retry_limit=0)

def adb_battery_level():
    (success, result) = adb_shell('cat /sys/class/power_supply/battery/capacity')
    result = 0 if not success else int(result)

    return result

###################
## AAPT WRAPPERS ##
###################
def aapt_call(command, *args):
    global aapt

    assert aapt is not None, 'SDK configuration not yet initialized, need to init() first'
    aapt_cmd = [aapt, command]
    aapt_cmd.extend(args)
    log('AAPT', aapt_cmd)
    return subprocess.check_output(aapt_cmd, stderr=subprocess.STDOUT).decode('UTF-8', 'backslashreplace')

last_badging_apk = None
last_badging  = None
def aapt_badging(apk_file):
    global last_badging_apk, last_badging
    if last_badging_apk is None or apk_file != last_badging_apk:
        last_badging = aapt_call('d', 'badging', apk_file)
        last_badging_apk = apk_file
    return last_badging

def aapt_permissions(apk_file):
    assert os.path.isfile(apk_file), '%s is not a valid APK path' % apk_file
    output = aapt_badging(apk_file)

    lines = output.split('\n')
    permissions = [x.split('name=')[1].strip("'") for x in lines if x.startswith('uses-permission:')]

    return permissions

def aapt_package(apk_file):
    assert os.path.isfile(apk_file), '%s is not a valid APK path' % apk_file
    output = aapt_badging(apk_file)

    lines = output.split('\n')
    package = [x for x in lines if x.startswith('package: name=')]
    assert len(package) == 1, 'More than one aapt d badging line starts with "package: name="'
    package = package[0].split('name=')[1].split(' versionCode=')[0].strip("'")

    return package

def aapt_version_code(apk_file):
    assert os.path.isfile(apk_file), '%s is not a valid APK path' % apk_file
    output = aapt_badging(apk_file)

    lines = output.split('\n')
    package = [x for x in lines if x.startswith('package: name=')]
    assert len(package) == 1, 'More than one aapt d badging line starts with "package: name="'
    version_code= package[0].split('versionCode=')[1].split(' versionName=')[0].strip("'")

    return version_code
