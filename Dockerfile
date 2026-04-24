# Use a Java 8 image
FROM reverie/armhf-alpine-oracle-jdk

RUN apk --update add curl g++ make libusb-dev bash &&\
	mkdir /phidget &&\
	curl -jksSL "https://www.phidgets.com/downloads/phidget21/libraries/linux/libphidget/libphidget_2.1.8.20170607.tar.gz" | tar -xzf - -C /phidget &&\
	cd /phidget/libphidget-2.1.8.20170607 &&\
	./configure && make && make install &&\
	rm -rf /phidget/libphidget-2.1.8.20170607 &&\
	apk del curl g++ make

# Make port 80 available to the world outside this container
EXPOSE 8080

# Define environment variable
ENV NAME Lapras
ENV LD_LIBRARY_PATH /usr/lib
ENV CONFIG_URL http://lapras.kaist.ac.kr/conf/default.conf

# Copy the current directory contents into the container at /lapras-agent
ADD ./lapras-agents/build/install /lapras-agent

# Run app.py when the container launches
WORKDIR /lapras-agent
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["echo $CONFIG_URL;/bin/bash run.sh console LaprasAgent \"${CONFIG_URL}\""]