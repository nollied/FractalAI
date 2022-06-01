import time
import numpy as np
import copy
from typing import Callable
from IPython.core.display import clear_output
from fractalai.model import DiscreteModel
from fractalai.swarm import Swarm, DynamicTree


class FractalMC(Swarm):

    def __init__(self, env, model, n_walkers: int=100, balance: float=1.,
                 reward_limit: float=None, samples_limit: int=None, render_every: int=None,
                 accumulate_rewards: bool=True, dt_mean: float=None, dt_std: float=None,
                 min_dt: int=1, custom_reward: Callable=None, custom_end: Callable=None,
                 process_obs: Callable=None, custom_skipframe: Callable=None,
                 keep_best: bool=False,  can_win: bool=False,
                 skip_initial_frames: int=0,
                 max_samples_step: int=None, time_horizon: int=40,
                 min_horizon: int=1, update_parameters: bool=False):
        """
        :param env: Environment that will be sampled.
        :param model: Model used for sampling actions from observations.
        :param n_walkers: Number of walkers that the swarm will use
        :param balance: Balance coefficient for the virtual reward formula.
        :param reward_limit: Maximum reward that can be reached before stopping the swarm.
        :param samples_limit: Maximum number of time the Swarm can sample the environment
         befors stopping.
        :param render_every: Number of iterations that will be performed before printing the Swarm
         status.
        :param accumulate_rewards: Use the accumulated reward when scoring the walkers.
                                  False to use instantaneous reward.
        :param dt_mean: Mean skipframe used for exploring.
        :param dt_std: Standard deviation for the skipframe. Sampled from a normal distribution.
        :param min_dt: Minimum skipframe to be used by the swarm.
        :param custom_reward: Callable for calculating a custom reward function.
        :param custom_end: Callable for calculating custom boundary conditions.
        :param process_obs: Callable for doing custom observation processing.
        :param custom_skipframe: Callable for sampling the skipframe values of the walkers.
        :param keep_best: Keep track of the best accumulated reward found so far.
        :param can_win: If the game can be won when a given score is achieved, set to True. Meant
        to be used with Atari games like Boxing, Pong, IceHockey, etc.
        :param skip_initial_frames: Skip n frame when the game begins.
        :param max_samples_step:  Maximum number of steps to be sampled per action.
        :param time_horizon: Desired path length allowed when calculating a step.
        :param min_horizon: Minimum path length allowed when calculating a step.
        :param update_parameters: Enable non-linear feedback loops to adjust internal params.
        """

        self.skip_initial_frames = skip_initial_frames
        self.max_walkers = n_walkers
        self.time_horizon = time_horizon
        self.max_samples = max_samples_step
        self.min_horizon = min_horizon

        _max_samples = max_samples_step if max_samples_step is not None else 1e10
        samples_limit = samples_limit if samples_limit is not None else 1e10
        self._max_step_total = max(_max_samples, samples_limit)
        self._max_samples_step = min(_max_samples, n_walkers * time_horizon)

        super(FractalMC, self).__init__(env=env, model=model, n_walkers=self.max_walkers,
                                        balance=balance, reward_limit=reward_limit,
                                        samples_limit=self._max_samples_step,
                                        render_every=render_every, custom_reward=custom_reward,
                                        custom_end=custom_end, dt_mean=dt_mean, dt_std=dt_std,
                                        keep_best=keep_best,
                                        accumulate_rewards=accumulate_rewards, min_dt=min_dt,
                                        can_win=can_win, process_obs=process_obs,
                                        custom_skipframe=custom_skipframe)
        self.init_ids = np.zeros(self.n_walkers).astype(int)
        self._update_parameters = update_parameters

        self._save_steps = []
        self._agent_reward = 0
        self._last_action = None

        self.tree = DynamicTree()

    @property
    def init_actions(self):
        return self.data.get_actions(self.init_ids)

    def init_swarm(self, state: np.ndarray=None, obs: np.ndarray=None):

        super(FractalMC, self).init_swarm(state=state, obs=obs)
        self.init_ids = np.zeros(self.n_walkers).astype(int)

    def clone(self):
        super(FractalMC, self).clone()
        if self._clone_idx is None:
            return
        self.init_ids = np.where(self._will_clone, self.init_ids[self._clone_idx], self.init_ids)

    def weight_actions(self) -> np.ndarray:
        """Gets an approximation of the Q value function for a given state.

        It weights the number of times a given initial action appears in each state of the swarm.
        The the proportion of times each initial action appears on the swarm, is proportional to
        the Q value of that action.
        """

        if isinstance(self._model, DiscreteModel):
            # return self.init_actions[self.rewards.argmax()]
            counts = np.bincount(self.init_actions, minlength=self._env.n_actions)
            return np.argmax(counts)
        vals = self.init_actions.sum(axis=0)
        return vals / self.n_walkers

    def update_data(self):
        init_actions = list(set(np.array(self.init_ids).astype(int)))
        walker_data = list(set(np.array(self.walkers_id).astype(int)))
        self.data.update_values(set(walker_data + init_actions))

    def run_swarm(self, state: np.ndarray=None, obs: np.ndarray=None, print_swarm: bool=False, print_info: bool=False):
        """
        Iterate the swarm by evolving and cloning each walker until a certain condition
        is met.
        :return:
        """
        self.reset()
        self.init_swarm(state=state, obs=obs)
        while not self.stop_condition():
            # We calculate the clone condition, and then perturb the walkers before cloning
            # This allows the deaths to recycle faster, and the Swarm becomes more flexible
            if self._i_simulation > 1:
                self.clone_condition()
            self.step_walkers(print_info=print_info)
            if self._i_simulation > 1:
                self.clone()
            elif self._i_simulation == 0:
                self.init_ids = self.walkers_id.copy()
            self._i_simulation += 1
            if self.render_every is not None and self._i_simulation % self.render_every == 0:
                self._env.render()
            if print_swarm:
                print(self)
                clear_output(True)

        if print_swarm:
            print(self)

    def _update_n_samples(self):
        """This will adjust the number of samples we make for calculating an state swarm. In case
        we are doing poorly the number of samples will increase, and it will decrease if we are
        sampling further than the minimum mean time desired.
        """
        limit_samples = self._max_samples_step / np.maximum(1e-7, self.balance)
        # Round and clip
        limit_clean = int(np.clip(np.ceil(limit_samples), 2, self.max_samples))
        self._max_samples_step = max(limit_clean, self.n_walkers * self.min_horizon)

    def _update_n_walkers(self):
        """The number of parallel trajectories used changes every step. It tries to use enough
         swarm to make the mean time of the swarm tend to the minimum mean time selected.
         """
        new_n = self.n_walkers * self.balance
        new_n = int(np.clip(np.ceil(new_n), 2, self.max_walkers))
        self.n_walkers = new_n

    def _update_balance(self):
        """The balance parameter regulates the balance between how much you weight the distance of
        each state (exploration) with respect to its score (exploitation).

        A balance of 1 would mean that the computational resources assigned to a given decision
        have been just enough to reach the time horizon. This means that we can assign the same
        importance to exploration and exploitation.

        A balance lower than 1 means that we are not reaching the desired time horizon. This
        means that the algorithm is struggling to find a valid solution. In this case exploration
        should have more importance than exploitation. It also shows that we need to increase the
        computational resources.

        A balance higher than 1 means that we have surpassed the time horizon. This
        means that we are doing so well that we could use less computational resources and still
        meet the time horizon. This also means that we can give exploitation more importance,
        because we are exploring the state space well.
        """
        self.balance = self.times.mean() / self.time_horizon

    def update_parameters(self):
        """Here we update the parameters of the algorithm in order to maintain the average time of
        the state swarm the closest to the minimum time horizon possible.
        """
        self._save_steps.append(int(self._n_samples_done))  # Save for showing while printing.
        self._update_balance()
        if self.balance >= 1:  # We are doing great
            if self.n_walkers == self.max_walkers:
                self._update_n_samples()  # Decrease the samples so we can be faster.
            else:
                self._update_n_walkers()  # Thi will increase the number of swarm

        else:  # We are not arriving at the desired time horizon.
            if self._max_samples_step == self.max_samples:
                self._update_n_walkers()  # Reduce the number of swarm to avoid useless clones.
            else:
                self._update_n_samples()  # Increase the amount of computation.

    def stop_condition(self) -> bool:
        """This sets a hard limit on maximum samples. It also Finishes if all the walkers are dead,
         or the target score reached.
         """
        stop_hard = self._n_samples_done > self._max_samples_step
        stop_score = False if self.reward_limit is None else \
            self.rewards.max() >= self.reward_limit
        stop_terminal = self._end_cond.all()
        # Define game status so the user knows why game stopped. Only used when printing the Swarm
        if stop_hard:
            self._game_status = "Sample limit reached."
        elif stop_score:
            self._game_status = "Score limit reached."
        elif stop_terminal:
            self._game_status = "All the walkers died."
        else:
            self._game_status = "Playing..."
        return stop_hard or stop_score or stop_terminal

    def recover_game(self, index=None) -> tuple:
        """
        By default, returns the game sampled with the highest score.
        :param index: id of the leaf where the returned game will finish.
        :return: a list containing the observations of the target sampled game.
        """
        if index is None:
            index = self.walkers_id[self.rewards.argmax()]
        return self.tree.get_branch(index)

    def render_game(self, index=None, sleep: float=0.02):
        """Renders the game stored in the tree that ends in the node labeled as index."""
        idx = max(list(self.tree.data.nodes)) if index is None else index
        states, actions, dts = self.recover_game(idx)
        for state, action, dt in zip(states, actions, dts):
            self._env.step(action, state=state, n_repeat_action=dt)
            self._env.render()
            time.sleep(sleep)

    def estimate_distributions(self, state, obs):
        self.run_swarm(state=copy.deepcopy(state), obs=obs)
        self.update_parameters()
        rewards = self.get_expected_reward()
        if isinstance(self._model, DiscreteModel):
            # return self.init_actions[self.rewards.argmax()]
            counts = np.bincount(self.init_actions, minlength=self._env.n_actions)
            return counts / counts.sum(), rewards
        vals = self.init_actions.sum(axis=0)
        probs = vals / self.n_walkers

        return probs, rewards

    def get_expected_reward(self):
        init_act = self.init_actions
        max_rewards = np.array([self.rewards[init_act == i].max() if
                                len(self.rewards[init_act == i]) > 0 else 0
                                for i in range(self.env.n_actions)])
        return max_rewards
        # TODO: Adapt again for continous control problems. Open an issue if you need it.
        max_r = max_rewards.max()
        min_r = max_rewards.min()
        div = (max_r - min_r)
        normed = (max_rewards - min_r) / div if div != 0 else 1 + max_rewards

        return normed / normed.sum()

    def _skip_initial_frames(self) -> tuple:
        state, obs = self._env.reset(return_state=True)
        i_step, self._agent_reward, end = 0, 0, False
        info = {}
        _reward = 0
        for i in range(self.skip_initial_frames):
            i_step += 1
            action = 0
            state, obs, _reward, _end, info = self._env.step(state=state, action=action,
                                                             n_repeat_action=self.min_dt)
            self.tree.append_leaf(i_step, parent_id=i_step - 1,
                                  state=state, action=action, dt=self._env.n_repeat_action)
            self._agent_reward += _reward
            self._last_action = action
            end = info.get("terminal", _end)
            if end:
                break
        return state, obs, _reward, end, info, i_step

    def run_agent(self, render: bool = False, print_swarm: bool=False):
        """

        :param render:
        :param print_swarm:
        :return:
        """
        self.tree.reset()
        state, obs, _reward, end, info, i_step = self._skip_initial_frames()
        self._save_steps = []

        self.tree.append_leaf(i_step, parent_id=i_step - 1,
                              state=state, action=0, dt=1)

        while not end and self._agent_reward < self.reward_limit:
            i_step += 1
            self.run_swarm(state=copy.deepcopy(state), obs=obs)
            action = self.weight_actions()

            state, obs, _reward, _end, info = self._env.step(state=state, action=action,
                                                             n_repeat_action=self.min_dt)
            self.tree.append_leaf(i_step, parent_id=i_step - 1,
                                  state=state, action=action, dt=self._env.n_repeat_action)
            self._agent_reward += _reward
            self._last_action = action
            end = info.get("terminal", _end)

            if render:
                self._env.render()
            if print_swarm:
                print(self)
                clear_output(True)
            if self._update_parameters:
                self.update_parameters()
