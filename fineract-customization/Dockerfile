# Multi-stage Dockerfile for Customized Apache Fineract 1.9.0
FROM eclipse-temurin:17-jdk-jammy AS builder

WORKDIR /fineract

# Install build dependencies & git
RUN apt-get update && apt-get install -y --no-install-recommends git patch && rm -rf /var/lib/apt/lists/*

# Clone pinned tag 1.9.0
ARG FINERACT_TAG=1.9.0
RUN git clone --depth 1 --branch ${FINERACT_TAG} https://github.com/apache/fineract.git .

# Apply customization patch
COPY patch/ /fineract/patch/
RUN if [ -f /fineract/patch/fineract-gl-sync.patch ]; then \
        git apply --ignore-whitespace /fineract/patch/fineract-gl-sync.patch; \
    fi

# Build Fineract Boot JAR without running external integration tests during image build
RUN ./gradlew bootJar -x test -x doclint -x spotlessCheck -x rat -x licenseMain -x licenseTest --no-daemon

# Runtime Stage
FROM eclipse-temurin:17-jre-jammy AS runtime

WORKDIR /opt/fineract

RUN useradd -m -u 1000 fineract && chown -R fineract:fineract /opt/fineract
USER fineract

COPY --from=builder --chown=fineract:fineract /fineract/fineract-provider/build/libs/fineract-provider*.jar app.jar

ENV PORT=8443
EXPOSE 8443

ENTRYPOINT ["java", "-Djava.security.egd=file:/dev/./urandom", "-jar", "app.jar"]

