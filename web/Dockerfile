FROM nikolaik/python-nodejs:python3.12-nodejs22-bullseye

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install dependencies
RUN apt-get update && \
    apt-get install -y libmagic1 poppler-utils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /code

# Copy and install Python dependencies
COPY requirements.in ./
RUN uv pip compile requirements.in -o requirements.txt && \
    uv pip sync requirements.txt --no-cache-dir --compile-bytecode --system

# Copy package.json and package-lock.json for frontend dependencies
COPY package.json package-lock.json ./
RUN npm install

# Copy the rest of the application code
COPY . .
