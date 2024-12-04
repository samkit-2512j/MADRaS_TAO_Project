#!/bin/bash
# Subsequent runs: check if torcs is running and start both if needed
while true;
do
	ps cax | grep torcs > /dev/null
	if [ $? -eq 0 ]; then
	: echo "Process is running."
	else
	
	echo "Process is not running."
	torcs & ./autostart.sh      	  	  
	fi
done;






