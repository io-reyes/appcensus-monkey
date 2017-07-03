#!/usr/local/bin/bash

set +e

function logcheck() {
    # Identify logs that don't have a "logcat start" line
    if [ $# -eq 1 ] && [ -s $1 ]; then
        LOG_PATH=$1

        if grep -q 'logcat start' $LOG_PATH; then
            return 0
        fi
    fi

    return 1
}

function run() {
    APK_PATH=/var/coppa/apks/
    MONKEY=/var/coppa/appcensus-monkey/monkey.py

    # Package only, take the biggest version code
    if [ $# -eq 1 ]; then
        PACKAGE=$1
        VCODE=`ls $APK_PATH/$PACKAGE/ | tail -n 1` > /dev/null

        if [ ! $? -eq 0 ]; then
            VCODE="thiswillfail$RANDOM"
        fi

    elif [ $# -eq 2 ]; then
        PACKAGE=$1
        VCODE=$2

    fi

    if [ -d $APK_PATH/$PACKAGE/$VCODE ]; then
        APK=$APK_PATH/$PACKAGE/$VCODE/$PACKAGE-$VCODE.apk
        CONFIG=special.config

        echo "python3 $MONKEY $CONFIG $APK ."
        python3 $MONKEY --apk $APK $CONFIG .

        # Note failed results
        if ! logcheck $PACKAGE/$VCODE/**/*.log; then
            (>&2 echo "WARNING: $PACKAGE-$VCODE run did not produce logs")
            echo "$PACKAGE" >> nolog.error
        fi
    else
        (>&2 echo "ERROR: APK for package $PACKAGE version $VCODE not found")
        echo "$PACKAGE" >> notfound.error
    fi

}

if [ $# -eq 1 ]; then
    INP=$1

    # Pre-read the whole input file
    declare -a LINES
    mapfile -t LINES < $INP

    # Run all inputs
    for LINE in "${LINES[@]}"; do
        # Remove quotation marks, convert commas to spaces
        LINE=`echo $LINE | tr -d '"' | tr ',' ' '`

        run $LINE
    done

    # Shut down the phone
    adb shell reboot -p
fi

