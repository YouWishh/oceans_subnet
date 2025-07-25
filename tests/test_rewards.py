from validator.rewards import RewardCalculator

def test_reward_vector_round_trip():
    # Prepare a fake metagraph with 3 miners
    class M: uids = [0, 1, 2]
    metagraph = M()

    # Fake cache
    from types import SimpleNamespace
    cache = SimpleNamespace(
        subnet_weights={10: 0.5, 11: 0.5},
        liquidity={
            10: {0: 100, 1: 0,  2: 100},
            11: {0: 0,   1: 50, 2: 50},
        },
    )

    rc = RewardCalculator(cache)
    w = rc.compute(metagraph=metagraph)

    # Two quick assertions
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert w == {0: 0.25, 1: 0.25, 2: 0.50}
