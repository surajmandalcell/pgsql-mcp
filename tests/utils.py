import logging
import os
import time
from collections.abc import Generator
from collections.abc import Mapping
from pathlib import Path
from typing import Never

import docker
import pytest
from docker import errors as docker_errors

logger = logging.getLogger(__name__)

POSTGRES_IMAGE_ENV: str = "PGSQL_MCP_TEST_POSTGRES_IMAGE"
SUPPORTED_POSTGRES_IMAGES: tuple[str, ...] = tuple(f"postgres:{major}" for major in range(14, 19))
DEFAULT_POSTGRES_IMAGES: tuple[str, ...] = ("postgres:15", "postgres:16")


def configured_postgres_images(environment: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return the validated PostgreSQL image selection for integration tests."""
    source = os.environ if environment is None else environment
    configured = source.get(POSTGRES_IMAGE_ENV)
    if configured is None or not configured.strip():
        return DEFAULT_POSTGRES_IMAGES

    image = configured.strip()
    if image not in SUPPORTED_POSTGRES_IMAGES:
        choices = ", ".join(SUPPORTED_POSTGRES_IMAGES)
        raise RuntimeError(f"{POSTGRES_IMAGE_ENV} must be one of: {choices}")
    return (image,)


def fail_or_skip_postgres_setup(
    message: str,
    environment: Mapping[str, str] | None = None,
) -> Never:
    """Fail dedicated compatibility jobs while allowing local no-Docker skips."""
    source = os.environ if environment is None else environment
    if source.get(POSTGRES_IMAGE_ENV):
        pytest.fail(message)
    pytest.skip(message)


def create_postgres_container(version: str) -> Generator[tuple[str, str], None, None]:
    """Create a PostgreSQL container of specified version and return its connection string."""
    try:
        client = docker.from_env()
        client.ping()
    except (docker_errors.DockerException, ConnectionError):
        fail_or_skip_postgres_setup("Docker is not available")

    # Extract PostgreSQL version number
    pg_version = version.split(":")[1] if ":" in version else version

    # Define custom image name with HypoPG
    custom_image_name = f"postgres-hypopg:{pg_version}"

    container_name = f"postgres-crystal-test-{version.replace(':', '_')}-{os.urandom(4).hex()}"
    current_dir = Path(__file__).parent.absolute()

    logger.info(f"Setting up PostgreSQL {pg_version} with HypoPG")

    # Build custom Docker image with HypoPG if it doesn't exist
    try:
        # Check if custom image already exists
        client.images.get(custom_image_name)
        logger.info(f"Using existing Docker image: {custom_image_name}")
    except docker_errors.ImageNotFound:
        # Build the image
        logger.info(f"Building custom Docker image: {custom_image_name}")
        try:
            dockerfile_path = current_dir / "Dockerfile.postgres-hypopg"
            if not dockerfile_path.exists():
                logger.error(f"Dockerfile not found at {dockerfile_path}")
                fail_or_skip_postgres_setup(f"Required Dockerfile not found: {dockerfile_path}")

            # Build the image
            client.images.build(
                path=str(current_dir),
                dockerfile="Dockerfile.postgres-hypopg",
                buildargs={"PG_VERSION": pg_version, "PG_MAJOR": pg_version},
                tag=custom_image_name,
                rm=True,
            )
            logger.info(f"Successfully built image {custom_image_name}")
        except Exception as error:
            logger.error(f"Failed to build Docker image: {error}")
            fail_or_skip_postgres_setup(f"Failed to build Docker image: {error}")

    postgres_password = "test_password"
    postgres_db = "test_db"

    # Create container with more verbose logging
    container = client.containers.run(
        custom_image_name,
        name=container_name,
        environment={
            "POSTGRES_PASSWORD": postgres_password,
            "POSTGRES_DB": postgres_db,
            "POSTGRES_HOST_AUTH_METHOD": "trust",  # Make authentication easier in tests
        },
        ports={"5432/tcp": ("127.0.0.1", 0)},  # Let Docker assign a random port
        command=[
            "-c",
            "shared_preload_libraries=pg_stat_statements",
            "-c",
            "pg_stat_statements.track=all",
            "-c",
            "log_min_messages=info",  # More verbose logging
            "-c",
            "log_statement=all",  # Log all SQL statements
        ],
        detach=True,
    )

    logger.info(f"Container {container_name} started, waiting for PostgreSQL to be ready")

    try:
        # Wait for container to start and get logs
        time.sleep(2)  # Give container a moment to start
        container.reload()

        # Check if container is running
        if container.status != "running":
            logs = container.logs().decode("utf-8")
            logger.error(f"Container {container_name} failed to start. Logs:\n{logs}")
            fail_or_skip_postgres_setup(f"PostgreSQL container failed to start: {logs[:500]}...")

        # Get assigned port
        port = container.ports["5432/tcp"][0]["HostPort"]

        # Wait for PostgreSQL to be ready
        deadline = time.time() + 60  # Increased timeout to 60 seconds
        is_ready = False
        last_error = None

        while time.time() < deadline and not is_ready:
            try:
                exit_code, output = container.exec_run("pg_isready")
                if exit_code == 0:
                    logger.info(f"PostgreSQL in container {container_name} is ready")
                    is_ready = True
                    break
                last_error = output.decode("utf-8")
                logger.warning(f"PostgreSQL not ready yet: {last_error}")
            except Exception as error:
                last_error = str(error)
                logger.warning(f"Error checking if PostgreSQL is ready: {error}")

            # Get container logs for debugging
            if time.time() - deadline + 60 > 50:  # Log when we're close to timeout
                logs = container.logs().decode("utf-8")
                logger.warning(f"Still waiting for PostgreSQL. Container logs:\n{logs[-2000:]}")

            time.sleep(2)

        if not is_ready:
            logs = container.logs().decode("utf-8")
            logger.error(f"Timeout waiting for PostgreSQL. Container logs:\n{logs[-2000:]}")
            fail_or_skip_postgres_setup(f"Timeout waiting for PostgreSQL to start: {last_error}")

        connection_string = f"postgresql://postgres:{postgres_password}@localhost:{port}/{postgres_db}"
        logger.info(f"PostgreSQL connection string: {connection_string}")

        yield connection_string, version

    except Exception as error:
        logger.error(f"Error setting up PostgreSQL container: {error}")
        # Get container logs for debugging
        try:
            logs = container.logs().decode("utf-8")
            logger.error(f"Container logs:\n{logs}")
        except Exception:
            pass
        raise

    finally:
        logger.info(f"Stopping and removing container {container_name}")
        try:
            container.stop(timeout=1)
            container.remove(v=True)
        except Exception as error:
            logger.warning(f"Error cleaning up container {container_name}: {error}")
