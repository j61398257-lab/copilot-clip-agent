FROM eclipse-temurin:21-jre-jammy

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl unzip ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ARG OPENTCS_VERSION=7.5.0
RUN curl -fsSL -o /tmp/opentcs.zip \
      "https://github.com/openTCS/opentcs/releases/download/v${OPENTCS_VERSION}/opentcs-${OPENTCS_VERSION}-bin.zip" \
 && unzip -q /tmp/opentcs.zip -d /opt/opentcs-dist \
 && rm /tmp/opentcs.zip \
 && find /opt/opentcs-dist -name startKernel.sh -exec chmod +x {} \;

COPY opentcs-kernel.properties /opt/opentcs-kernel.properties
COPY model.xml /opt/opentcs-model.xml
EXPOSE 55200
ENV JAVA_TOOL_OPTIONS="-Xmx384m"
CMD ["sh", "-c", "dir=$(find /opt/opentcs-dist -type f -name startKernel.sh -printf '%h\\n' | head -n 1) && cp /opt/opentcs-kernel.properties \"$dir/config/opentcs-kernel.properties\" && mkdir -p \"$dir/data\" && cp /opt/opentcs-model.xml \"$dir/data/model.xml\" && cd \"$dir\" && exec sh ./startKernel.sh"]
