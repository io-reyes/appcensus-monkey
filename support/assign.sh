#!/bin/bash

if [ $# -eq 2 ]; then
    APP_LIST=$1
    ASSIGN_COUNT=$2

    LINE_COUNT=`wc -l < $APP_LIST | tr -d ' '`
    DIV_COUNT=`expr $LINE_COUNT \/ $ASSIGN_COUNT`
    #MOD_COUNT=`expr $LINE_COUNT \% $ASSIGN_COUNT`
    SPLIT_COUNT=`expr $DIV_COUNT \+ 1`

    SHUF_LIST=$APP_LIST.shuf
    shuf $APP_LIST > $SHUF_LIST

    split -l $SPLIT_COUNT $SHUF_LIST
    rm $SHUF_LIST
fi
