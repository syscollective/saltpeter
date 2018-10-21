from ubuntu:xenial

MAINTAINER Syscollective SRL 

COPY ./ /opt/saltpeter/

# Add the Salt repo
RUN apt-get update && \
    apt-get install -y -o DPkg::Options::=--force-confold wget

RUN wget -O - https://repo.saltstack.com/apt/ubuntu/16.04/amd64/latest/SALTSTACK-GPG-KEY.pub | apt-key add - && \
    echo 'deb http://repo.saltstack.com/apt/ubuntu/16.04/amd64/latest xenial main' > /etc/apt/sources.list.d/saltstack.list && \
    apt-get update

# Install Salt master and minion
RUN apt-get install -y -o DPkg::Options::=--force-confold \
	salt-master

# Install pip and python-dev
RUN apt-get install -y -o DPkg::Options::=--force-confold \
	python-dev python-pip

# Configure salt master
RUN echo "auto_accept: True" > /etc/salt/master.d/auto_accept.conf
#RUN echo "master: saltpeter-master" > /etc/salt/minion.d/masters.conf

# Install Saltperer's dependencies
RUN pip install -r /opt/saltpeter/requirements.txt

EXPOSE 4505 4506

#ENTRYPOINT ["/usr/bin/salt-master", "-d", "&&", "/opt/saltpeter/saltpeter/saltpeter.py"]
ENTRYPOINT ["/opt/saltpeter/entrypoint.sh"]
