Syntax:


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
#number of machines to execute on, optional, 0 means all

