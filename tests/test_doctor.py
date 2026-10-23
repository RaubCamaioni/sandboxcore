"""sandboxcore-doctor's checks, fed with what a good and a broken system report."""

from sandboxcore import doctor

GOOD_INFO = {
    "host": {
        "security": {"rootless": True},
        "cgroupVersion": "v2",
        "cgroupManager": "systemd",
        "cgroupControllers": ["cpu", "cpuset", "io", "memory", "pids"],
    },
    "store": {"graphDriverName": "overlay"},
}


def statuses(checks):
    return {c.name: c.status for c in checks}


def test_a_good_system_passes():
    assert set(statuses(doctor.check_podman_info(GOOD_INFO)).values()) == {"ok"}
    assert doctor.check_user(1000).status == "ok"
    assert doctor.check_subids("subuid", "app", 1000, "other:100000:65536\napp:165536:65536\n").status == "ok"
    assert doctor.check_userns("1", "28633").status == "ok" and doctor.check_userns(None, None).status == "ok"
    assert {c.status for c in doctor.check_selftest(137, 255)} == {"ok"}


def test_root_and_rootful_podman_fail():
    assert doctor.check_user(0).status == "FAIL"
    info = {**GOOD_INFO, "host": {**GOOD_INFO["host"], "security": {"rootless": False}}}
    assert statuses(doctor.check_podman_info(info))["podman rootless"] == "FAIL"


def test_undelegated_controllers_fail_with_the_fix():
    info = {**GOOD_INFO, "host": {**GOOD_INFO["host"], "cgroupControllers": ["memory", "pids"]}}
    check = next(c for c in doctor.check_podman_info(info) if c.name == "delegated controllers")
    assert check.status == "FAIL" and "cpu" in check.detail and "Delegate=" in check.fix


def test_cgroup_v1_and_cgroupfs_fail():
    info = {**GOOD_INFO, "host": {**GOOD_INFO["host"], "cgroupVersion": "v1", "cgroupManager": "cgroupfs"}}
    s = statuses(doctor.check_podman_info(info))
    assert s["cgroup v2"] == "FAIL" and s["cgroup manager"] == "FAIL"


def test_subid_ranges():
    assert doctor.check_subids("subuid", "app", 1000, "").status == "FAIL"
    assert doctor.check_subids("subuid", "app", 1000, "app:2000:1000\n").status == "FAIL"  # uid 1001 unmappable
    assert doctor.check_subids("subuid", "app", 1000, "1000:2000:60000\n").status == "warn"  # by uid, below 65536
    assert doctor.check_subids("subgid", "app", 1000, "app:x:y\n").status == "FAIL"  # malformed lines ignored


def test_disabled_user_namespaces_fail():
    assert doctor.check_userns("0\n", None).status == "FAIL"
    assert doctor.check_userns(None, "0\n").status == "FAIL"


def test_a_container_that_never_started_is_not_blamed_on_the_limits():
    (check,) = doctor.check_selftest(126, 126, "Error: crun: sethostname: Operation not permitted")
    assert check.status == "FAIL" and "did not start" in check.detail and "sethostname" in check.detail


def test_a_nested_range_below_65536_is_fine_inside_a_container():
    assert doctor.check_subids("subuid", "podman", 1000, "podman:2000:60000\n", in_container=True).status == "ok"


def test_unenforced_limits_fail():
    s = statuses(doctor.check_selftest(0, 0))
    assert s == {"memory limit enforced": "FAIL", "timeout enforced": "FAIL"}
