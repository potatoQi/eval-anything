import fcntl
from abc import ABC, abstractmethod
from typing import ClassVar

import envweaver as ew

from ..sources import unpack


class Benchmark(ABC):
    name: str
    harness: str
    repo: str
    revision: str
    checksum: str
    size: int
    pool_directory = "pool"
    trials = 1
    harness_defaults: ClassVar[dict] = {}
    generation_only = False
    fixed_harness = False
    bindings = ()

    # Optional filter on paths relative to the pinned checkout.
    includes_source = None

    @abstractmethod
    def catalog(self, source):
        """Return the upstream task names."""

    @abstractmethod
    def prepare(self, pool, source, missing, proxy):
        """Prepare the benchmark environment and save missing tasks."""

    @abstractmethod
    def runtime(self, *, output, proxy, config, agent):
        """Configure Eval and return a context providing runtime harness bindings."""

    def resolve_revision(self, saved, proxy):
        """Return the source commit for this run."""
        return self.revision

    def fetch_source(self, source, proxy):
        unpack(
            f"https://codeload.github.com/{self.repo}/tar.gz/{self.revision}",
            self.checksum,
            source,
            proxy,
            include=self.includes_source,
        )

    def load_source(self, cache, proxy):
        source = cache / "sources" / self.revision
        self.fetch_source(source, proxy)
        return source

    def generate(self, cache, proxy):
        with (cache / "prepare.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            source = self.load_source(cache, proxy)
            names = self.catalog(source)
            if len(names) != self.size or len(set(names)) != self.size:
                raise ValueError("Incomplete or duplicate upstream task catalog")
            pool = ew.TaskPool(
                root=cache / self.pool_directory, runtime=ew.Runtime(root=cache / "runtime")
            )
            refs = {n: pool.root / n / "task.json" for n in names}
            missing = [n for n, path in refs.items() if not path.exists()]
            print(f"{self.name}: {len(names)} tasks, {len(missing)} to prepare", flush=True)
            self.prepare(pool, source, missing, proxy)
            return [pool.load(task_ref=ref) for ref in refs.values()]

    def agent_timeout(self, task):
        return task.metadata["agent_timeout"]

    def round_tasks(self, tasks, output, number):
        return tasks

    @abstractmethod
    def run_protocol(self, tasks, run_round):
        """Schedule this benchmark's rounds and return its pass metrics."""
