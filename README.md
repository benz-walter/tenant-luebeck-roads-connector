# Roads Synchronizer

The Roads Synchronizer is a small Python script that accesses a configured database, reads the schema and data of listed tables,
and pushes that information converted to JSON to an AMQP message broker, e.g. RabbitMQ, for further processing.

The following databases are supported:
* PostgreSQL
* Microsoft SQL Server

Others might work as well.

Release packages including a Helm chart can be found at https://github.com/benz-walter/tenant-luebeck-roads-connector.  
The image can be found at `ghcr.io/benz-walter/tenant-luebeck-roads-connector/roads-connector`.

The repository adheres to [semantic versioning](https://semver.org).

## Configuration

The application is configured mostly using environment variables:

| Name                  | Type       | Default       | Comment                                       |
|-----------------------|------------|---------------|-----------------------------------------------|
| DEBUG                 | bool       | false         |                                               |
| LOG_LEVEL             | str        | DEBUG or INFO |                                               |
| BROKER_HOST           | str        | localhost     |                                               |
| BROKER_PORT           | int        | 5672          |                                               |
| BROKER_QUEUE_NAME     | str        |               |                                               |
| DATABASE_URL          | str        |               |                                               |
| SYNC_TABLES           | list[str]  |               | Comma seperated string, e.g. "table1, table2" |
| SYNC_INTERVAL_MINUTES | int        | 30            |                                               |


## Usage

By default, the application runs once and exits afterward.  
However, it can be run continuously as a background process using `--background` as an argument, syncing data once per `SYNC_INTERVAL_MINUTES`.

### Running using Helm Chart / Kubernetes

The repository provides a Helm chart in directory `/chart`.
It will install the application using a `CronJob` resource and will pull the image from a public repository by default.

Default values can be found at `/chart/values.yaml` and can be adjusted by providing an additional values file while deploying the chart:

```bash
helm upgrade --install roads-connector [-n namespace] -f <custom-values-file> <path-to-chart>
```

### Running with docker compose
To run it using the docker compose utility (excluding development services):

First, copy `.env.defaults` to `.env` to simplify setting up environment variables, then:
```bash
docker compose run --rm --no-deps app /app/src/main.py
```

### Running with docker

```bash
# Build the container
docker build --target production -t <tag>
# Run the container
docker run --rm -e DATABASE_URL=... <tag> /app/src/main.py
```

## Development

The repository contains a docker-compose file to set up a local development environment.
It includes the following services:

* `app` - Application / synchronizer
* `rabbitmq` - Message broker
* `database` - Database

To build and run, type:
```bash
docker compose build
docker compose up
```

### RabbitMQ

The RabbitMQ webinterface is available at `http://localhost:15672`.  
Use the default credentials of `guest:guest` to login.

### Database

The database is a Microsoft SQL Server and will be available at `localhost:1433`.  
The default administrator is `sa`, and the password is specified as `MSSQL_SA_PASSWORD` within the docker-compose file.


## Maintenance

### Docker / Base image

To update the base images used, change `IMAGE_UV` and `IMAGE_PYTHON` within the `Dockerfile`.  
Afterward, rebuild the container with `docker compose build`.

### Python / Application
The application is programmed in Python and makes use of `uv` to maintain dependencies.  
Python itself is provided by the base docker image.

To update all dependencies, run:

```bash
# On your local machine
docker compose run --rm --no-deps app bash
# Within the container
cd /app
uv lock --upgrade
```

Afterward, rebuild the container with `docker compose build`.

See [Managing dependencies](https://docs.astral.sh/uv/concepts/projects/dependencies/),
as well as [Locking and Syncing](https://docs.astral.sh/uv/concepts/projects/sync/#locking-and-syncing) for more information. 


## Release

The `Dockerfile` provides multiple targets: `production` and `development`. `development` is used by default and inherits from `production`.

To build a new production image without development dependencies, run:
```bash
docker build --target production --tag <tag>
```
