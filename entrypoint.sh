#!/bin/bash

/usr/bin/salt-master -d

/opt/saltpeter/saltpeter/saltpeter.py -a -p 8888
