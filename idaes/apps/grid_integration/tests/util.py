#################################################################################
# The Institute for the Design of Advanced Energy Systems Integrated Platform
# Framework (IDAES IP) was produced under the DOE Institute for the
# Design of Advanced Energy Systems (IDAES), and is copyright (c) 2018-2021
# by the software owners: The Regents of the University of California, through
# Lawrence Berkeley National Laboratory,  National Technology & Engineering
# Solutions of Sandia, LLC, Carnegie Mellon University, West Virginia University
# Research Corporation, et al.  All rights reserved.
#
# Please see the files COPYRIGHT.md and LICENSE.md for full copyright and
# license information.
#################################################################################
from collections import deque
import pyomo.environ as pyo
import pandas as pd
from idaes.apps.grid_integration import Tracker
from idaes.apps.grid_integration import Bidder, SelfScheduler
from idaes.apps.grid_integration import DoubleLoopCoordinator
from idaes.apps.grid_integration.forecaster import AbstractPrescientPriceForecaster
from idaes.apps.grid_integration.model_data import (
    GeneratorModelData,
    ThermalGeneratorModelData,
)


class TestingModel:

    """
    Simple model object for testing.
    """

    def __init__(self, model_data):

        """
        Initializes the class object by building the thermal generator model.

        Arguments:

            horizon: the length of the planning horizon of the model.
            name: generator name
            pmin: the minimal capacity of the generator in MW
            pmax: the maximal capacity of the generator in MW

        Returns:
            None
        """

        if not isinstance(model_data, ThermalGeneratorModelData):
            raise TypeError(
                f"model_data must be an instance of ThermalGeneratorModelData."
            )
        self._model_data = model_data

        self.generator = self.model_data.gen_name
        self.result_list = []
        self.pmin = self.model_data.p_min
        self.pmax = self.model_data.p_max
        self.marginal_cost = self.model_data.p_cost[0][1]

    @property
    def model_data(self):
        return self._model_data

    def populate_model(self, b, horizon):

        """
        This function builds the model for a thermal generator.

        Arguments:
            plan_horizon: the length of the planning horizon of the model.
            segment_number: number of segments used in the piecewise linear
            production model.

        Returns:
            b: the constructed block.
        """

        ## define the sets
        b.HOUR = pyo.Set(initialize=range(horizon))

        ## define the parameters
        b.marginal_cost = pyo.Param(initialize=self.marginal_cost, mutable=False)

        # capacity of generators: upper bound (MW)
        b.Pmax = pyo.Param(initialize=self.pmax, mutable=False)

        # minimum power of generators: lower bound (MW)
        b.Pmin = pyo.Param(initialize=self.pmin, mutable=False)

        b.pre_P_T = pyo.Param(initialize=self.pmin, mutable=True)

        ## define the variables
        # power generated by thermal generator
        b.P_T = pyo.Var(b.HOUR, initialize=self.pmin, bounds=(self.pmin, self.pmax))

        ## Expression
        def prod_cost_fun(b, h):
            return b.P_T[h] * b.marginal_cost

        b.prod_cost_approx = pyo.Expression(b.HOUR, rule=prod_cost_fun)

        # total cost
        def tot_cost_fun(b, h):
            return b.prod_cost_approx[h]

        b.tot_cost = pyo.Expression(b.HOUR, rule=tot_cost_fun)

        return

    @staticmethod
    def _update_power(b, implemented_power_output):
        """
        This method updates the parameters in the ramping constraints based on
        the implemented power outputs.

        Arguments:
            b: the block that needs to be updated
            implemented_power_output: realized power outputs: []

         Returns:
             None
        """

        b.pre_P_T = round(implemented_power_output[-1], 2)

        return

    def update_model(self, b, implemented_power_output):

        """
        This method updates the parameters in the model based on
        the implemented power outputs, shut down and start up events.

        Arguments:
            b: the block that needs to be updated
            implemented_power_output: realized power outputs: []

         Returns:
             None
        """

        self._update_power(b, implemented_power_output)

        return

    @staticmethod
    def get_implemented_profile(b, last_implemented_time_step):

        """
        This method gets the implemented variable profiles in the last optimization
        solve.

        Arguments:
            b: the block.

            model_var: intended variable name in str.

            last_implemented_time_step: time index for the last implemented time step.

         Returns:
             profile: the intended profile, {unit: [...]}
        """

        implemented_power_output = deque(
            [pyo.value(b.P_T[t]) for t in range(last_implemented_time_step + 1)]
        )

        return {"implemented_power_output": implemented_power_output}

    @staticmethod
    def get_last_delivered_power(b, last_implemented_time_step):

        """
        Returns the last delivered power output.

        Arguments:
            None

        Returns:
            None
        """

        return pyo.value(b.P_T[last_implemented_time_step])

    def record_results(self, b, date=None, hour=None, **kwargs):

        """
        Record the operations stats for the model.

        Arguments:

            date: current simulation date.

            hour: current simulation hour.

        Returns:
            None

        """

        df_list = []

        for t in b.HOUR:

            result_dict = {}
            result_dict["Generator"] = self.generator
            result_dict["Date"] = date
            result_dict["Hour"] = hour

            # simulation inputs
            result_dict["Horizon [hr]"] = int(t)

            # model vars
            result_dict["Thermal Power Generated [MW]"] = float(
                round(pyo.value(b.P_T[t]), 2)
            )

            result_dict["Production Cost [$]"] = float(
                round(pyo.value(b.prod_cost_approx[t]), 2)
            )
            result_dict["Total Cost [$]"] = float(round(pyo.value(b.tot_cost[t]), 2))

            # calculate mileage
            if t == 0:
                result_dict["Mileage [MW]"] = float(
                    round(abs(pyo.value(b.P_T[t] - b.pre_P_T)), 2)
                )
            else:
                result_dict["Mileage [MW]"] = float(
                    round(abs(pyo.value(b.P_T[t] - b.P_T[t - 1])), 2)
                )

            for key in kwargs:
                result_dict[key] = kwargs[key]

            result_df = pd.DataFrame.from_dict(result_dict, orient="index")
            df_list.append(result_df.T)

        # save the result to object property
        # wait to be written when simulation ends
        self.result_list.append(pd.concat(df_list))

        return

    def write_results(self, path):

        """
        This methods writes the saved operation stats into an csv file.

        Arguments:
            path: the path to write the results.

        Return:
            None
        """

        pd.concat(self.result_list).to_csv(path, index=False)

    @property
    def power_output(self):
        return "P_T"

    @property
    def total_cost(self):
        return ("tot_cost", 1)


class TestingForecaster(AbstractPrescientPriceForecaster):
    """
    A fake forecaster class for testing.
    """

    def __init__(self, prediction):
        self.prediction = prediction

    def forecast_day_ahead_and_real_time_prices(
        self, date, hour, bus, horizon, n_samples
    ):
        rt_forecast = self.forecast_real_time_prices(
            date, hour, bus, horizon, n_samples
        )
        da_forecast = self.forecast_day_ahead_prices(
            date, hour, bus, horizon, n_samples
        )

        return da_forecast, rt_forecast

    def forecast_real_time_prices(self, date, hour, bus, horizon, n_samples):
        return self._forecast(horizon, n_samples, 0)

    def forecast_day_ahead_prices(self, date, hour, bus, horizon, n_samples):
        return self._forecast(horizon, n_samples, self.prediction)

    def _forecast(self, horizon, n_samples, prediction):
        return {i: [prediction] * horizon for i in range(n_samples)}

    def fetch_hourly_stats_from_prescient(self, prescient_hourly_stats):
        return

    def fetch_day_ahead_stats_from_prescient(self, uc_date, uc_hour, day_ahead_result):
        return


testing_generator_params = {
    "gen_name": "10_STEAM",
    "bus": "bus5",
    "p_min": 30,
    "p_max": 76,
    "min_down_time": 4,
    "min_up_time": 8,
    "ramp_up_60min": 120,
    "ramp_down_60min": 120,
    "shutdown_capacity": 30,
    "startup_capacity": 30,
    "initial_status": 9,
    "initial_p_output": 30,
    "production_cost_bid_pairs": [
        (30, 30),
        (45.3, 30),
        (60.7, 30),
        (76, 30),
    ],
    "startup_cost_pairs": [(4, 7355.42), (10, 10488.35), (12, 11383.41)],
    "fixed_commitment": None,
}

testing_model_data = ThermalGeneratorModelData(**testing_generator_params)
tracking_horizon = 4
day_ahead_bidding_horizon = 48
real_time_bidding_horizon = 4
n_scenario = 10
n_tracking_hour = 1
solver = pyo.SolverFactory("cbc")


def make_testing_forecaster():

    """
    Create a forecaster for testing.

    Arguments:
        None

    Returns:
        forecaster: a forecaster object for testing.
    """

    return TestingForecaster(prediction=30)


def make_testing_tracker():
    """
    Create a tracker for testing.

    Arguments:
        None

    Returns:
        thermal_tracker: a tracker object for testing.
    """

    tracking_model_object = TestingModel(model_data=testing_model_data)
    thermal_tracker = Tracker(
        tracking_model_object=tracking_model_object,
        tracking_horizon=tracking_horizon,
        n_tracking_hour=n_tracking_hour,
        solver=solver,
    )

    return thermal_tracker


def make_testing_bidder():

    """
    Create a bidder for testing.

    Arguments:
        None

    Returns:
        thermal_bidder: a bidder object for testing.
    """

    forecaster = make_testing_forecaster()

    bidding_model_object = TestingModel(model_data=testing_model_data)
    thermal_bidder = Bidder(
        bidding_model_object=bidding_model_object,
        day_ahead_horizon=day_ahead_bidding_horizon,
        real_time_horizon=real_time_bidding_horizon,
        n_scenario=n_scenario,
        solver=solver,
        forecaster=forecaster,
    )

    return thermal_bidder


def make_testing_selfscheduler():

    """
    Create a self-scheduler for testing.

    Arguments:
        None

    Returns:
        self_scheduler: a tracker object for testing.
    """

    forecaster = make_testing_forecaster()

    bidding_model_object = TestingModel(model_data=testing_model_data)
    self_scheduler = SelfScheduler(
        bidding_model_object=bidding_model_object,
        day_ahead_horizon=day_ahead_bidding_horizon,
        real_time_horizon=real_time_bidding_horizon,
        n_scenario=n_scenario,
        solver=solver,
        forecaster=forecaster,
        fixed_to_schedule=True,
    )

    return self_scheduler
