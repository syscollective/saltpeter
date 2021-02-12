### Description:

Saltpeter is a distributed cron implementation using salt as the remote execution layer.

It reads configuration from yaml files in a folder specified by -c (default /etc/saltpeter).


### Installation:

Pip install the git repository:
```
python3 -m pip install git+https://github.com/syscollective/saltpeter.git
```
Create a new service with `vim /etc/systemd/system/saltpeter` and add the following lines:
```
[Unit]
Description=Saltpeter distributed scheduled execution tool

[Service]
User=root
#WorkingDirectory=/opt/saltpeter/
ExecStart=/usr/local/bin/saltpeter -a -p 8888
Restart=on-failure
KillSignal=SIGTERM
SyslogIdentifier=saltpeter

[Install]
WantedBy=multi-user.target
```
Create the following folders:
```
mkdir /etc/saltpeter
mkdir /var/log/saltpeter
```
Start the service:
```
service saltpeter start
```


### Developing using Docker:

There is an example docker-compose file in examples which starts 1 container with salt-master and saltpeter and 2 additional salt-minion containers.

Just do:

```
cd examlples; docker-compose up
```

The master should come up and automatically accept the 2 minions (or however many) when they come up. Saltpeter will attempt to run the jobs it finds in examples/config and produce logs on stdout and in examples/logs/*.log .


### Config syntax:

```
example:
#name of the cron, has to be unique system-wide


    year: '*'
    mon: '*'
    dow: '*'
    dom: '*'
    hour: '1-23'
    min: '*/2'
    sec: 0,15,30,45
#time to run, same syntax as cron

    command: '/usr/bin/touch /tmp/example_cron; hostname -f'
#command to run
    user: 'root'
#user to run the command as on the target machine

    cwd: '/'
#change to this dir before executing

    targets: 'lon123*'
#expression to match target machines

    target_type: glob
#targeting type, can be one of the following:
#    glob - Bash glob completion - Default
#    pcre - Perl style regular expression
#    list - Python list of hosts
#    grain - Match based on a grain comparison
#    grain_pcre - Grain comparison with a regex
#    pillar - Pillar data comparison
#    pillar_pcre - Pillar data comparison with a regex
#    nodegroup - Match on nodegroup
#    range - Use a Range server for matching
#    compound - Pass a compound match string
#    ipcidr - Match based on Subnet (CIDR notation) or IPv4 address.
#Compound matching is best:
#https://docs.saltstack.com/en/latest/topics/targeting/compound.html

    number_of_targets: 0
#number of machines to execute on, optional, 0 means all;
#if set to 1, it will execute on only 1 machine randomly chosen from the available targets

    soft_timeout: 120
    hard_timeout: 240
# timeout values; soft_timeout is used only for monitoring purposes, it will only produce a line in the log so far
# hard_timeout will actually kill the job when exceeded

```
