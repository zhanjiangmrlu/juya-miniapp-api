import pytest

from juya_miniapp_api.infrastructure.analytics_events import validate_event


@pytest.mark.parametrize("event", ["CONTACT_SUBMITTED", "CONTACT_CHANGED"])
def test_contact_cohort_accepted_without_identifying_payload(event: str) -> None:
    # 功能:验证联系方式匿名分组事件不含可识别用户载荷
    # 参数:
    #     event: 需要收集或触发的业务事件名称
    # 返回:无返回值;断言失败时由pytest报告测试失败
    validate_event(event, "ALL", {"contact_cohort": "a" * 32})


@pytest.mark.parametrize("cohort", ["user-123", "a" * 31, "Z" * 32])
def test_invalid_contact_cohort_rejected(cohort: str) -> None:
    # 功能:验证无效联系方式分组被统计契约拒绝
    # 参数:
    #     cohort: 联系方式引导的匿名实验分组
    # 返回:无返回值;断言失败时由pytest报告测试失败
    with pytest.raises(ValueError):
        validate_event("CONTACT_SUBMITTED", "ALL", {"contact_cohort": cohort})
