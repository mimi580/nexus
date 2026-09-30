from app.orchestrator.loop import LoopController, LoopLimits


def test_iteration_ceiling_terminates_the_loop():
    controller = LoopController(LoopLimits(max_iterations=3))
    while controller.should_continue():
        controller.record_iteration("sig", cost=0.0, progressed=True)
    assert controller.state.stop_reason == "max_iterations"


def test_no_progress_terminates_the_loop():
    controller = LoopController(LoopLimits(max_iterations=100, min_progress_iterations=4))
    while controller.should_continue():
        controller.record_iteration("sig", cost=0.0, progressed=False)
    assert controller.state.stop_reason == "no_progress"


def test_cost_ceiling_terminates_the_loop():
    controller = LoopController(LoopLimits(max_cost_usd=0.05))
    while controller.should_continue():
        controller.record_iteration("sig", cost=0.02, progressed=True)
    assert controller.state.stop_reason == "max_cost"


def test_repeated_signatures_are_detected():
    controller = LoopController(LoopLimits(max_repeats_per_signature=2))
    signature = controller.signature("outreach", {"opportunity_id": "opp_1"})
    assert controller.is_repeat(signature) is False
    controller.record_iteration(signature, cost=0.0, progressed=False)
    controller.record_iteration(signature, cost=0.0, progressed=False)
    assert controller.is_repeat(signature) is True


def test_signature_is_order_independent():
    controller = LoopController()
    a = controller.signature("x", {"a": 1, "b": 2})
    b = controller.signature("x", {"b": 2, "a": 1})
    assert a == b
