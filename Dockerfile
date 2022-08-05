from debian:bullseye

MAINTAINER Syscollective SRL 

COPY ./ /opt/saltpeter/

# Install basic tools
RUN apt-get update && \
    apt-get install -y -o DPkg::Options::=--force-confold wget vim net-tools htop apt-utils apt-transport-https gnupg

# Install pip and python-dev
RUN apt-get install -y -o DPkg::Options::=--force-confold \
    python3-dev python3-pip

RUN pip3 install salt-master

# Configure salt master
RUN echo "auto_accept: True" > /etc/salt/master.d/auto_accept.conf
#RUN echo "master: saltpeter-master" > /etc/salt/minion.d/masters.conf
RUN echo "master: sp-master" > /etc/salt/minion.d/masters.conf

# Install Saltperer's dependencies
RUN pip3 install -r /opt/saltpeter/requirements.txt

EXPOSE 4505 4506

#ENTRYPOINT ["/usr/bin/salt-master", "-d", "&&", "/opt/saltpeter/saltpeter/saltpeter.py"]
ENTRYPOINT ["/opt/saltpeter/entrypoint.sh"]
