import unittest

import numpy as np
import os
from parameterized import parameterized
import tempfile

from qiskit import BasicAer
from test.common import QiskitAquaTestCase
from qiskit.aqua import Operator, QuantumInstance, QiskitAqua
from qiskit.aqua.input import EnergyInput
from qiskit.aqua.components.variational_forms import RY
from qiskit.aqua.components.optimizers import L_BFGS_B
from qiskit.aqua.components.initial_states import Zero
from qiskit.aqua.algorithms.adaptive import VQE
from qiskit.aqua.utils import CircuitCache


class TestCaching(QiskitAquaTestCase):

    def setUp(self):
        super().setUp()
        np.random.seed(50)
        pauli_dict = {
            'paulis': [{"coeff": {"imag": 0.0, "real": -1.052373245772859}, "label": "II"},
                       {"coeff": {"imag": 0.0, "real": 0.39793742484318045}, "label": "IZ"},
                       {"coeff": {"imag": 0.0, "real": -0.39793742484318045}, "label": "ZI"},
                       {"coeff": {"imag": 0.0, "real": -0.01128010425623538}, "label": "ZZ"},
                       {"coeff": {"imag": 0.0, "real": 0.18093119978423156}, "label": "XX"}
                       ]
        }
        qubit_op = Operator.load_from_dict(pauli_dict)
        self.algo_input = EnergyInput(qubit_op)

        backends = ['statevector_simulator', 'qasm_simulator']
        res = {}
        for backend in backends:
            params_no_caching = {
                'algorithm': {'name': 'VQE', 'operator_mode': 'matrix' if backend == 'statevector_simulator' else 'paulis'},
                'problem': {'name': 'energy',
                            'random_seed': 50,
                            'circuit_caching': False,
                            'skip_qobj_deepcopy': False,
                            'skip_qobj_validation': False,
                            'circuit_cache_file': None,
                            },
                'backend': {'provider': 'qiskit.BasicAer', 'name': backend},
            }
            if backend != 'statevector_simulator':
                params_no_caching['backend']['shots'] = 1000
            qiskit_aqua = QiskitAqua(params_no_caching, self.algo_input)
            res[backend] = qiskit_aqua.run()
        self.reference_vqe_result = res

    @parameterized.expand([
        ['statevector_simulator', True, True],
        ['qasm_simulator', True, True],
        ['statevector_simulator', True, False],
        ['qasm_simulator', True, False],
    ])
    def test_vqe_caching_via_run_algorithm(self, backend, caching, skip_qobj_deepcopy):
        skip_validation = True
        params_caching = {
            'algorithm': {'name': 'VQE', 'operator_mode': 'matrix' if backend == 'statevector_simulator' else 'paulis'},
            'problem': {'name': 'energy',
                        'random_seed': 50,
                        'circuit_caching': caching,
                        'skip_qobj_deepcopy': skip_qobj_deepcopy,
                        'skip_qobj_validation': skip_validation,
                        'circuit_cache_file': None,
                        },
            'backend': {'provider': 'qiskit.BasicAer', 'name': backend},
        }
        if backend != 'statevector_simulator':
            params_caching['backend']['shots'] = 1000
        qiskit_aqua = QiskitAqua(params_caching, self.algo_input)
        result_caching = qiskit_aqua.run()

        self.assertAlmostEqual(result_caching['energy'], self.reference_vqe_result[backend]['energy'])

        np.testing.assert_array_almost_equal(self.reference_vqe_result[backend]['eigvals'],
                                             result_caching['eigvals'], 5)
        np.testing.assert_array_almost_equal(self.reference_vqe_result[backend]['opt_params'],
                                             result_caching['opt_params'], 5)
        if qiskit_aqua.quantum_instance.has_circuit_caching:
            self.assertEqual(qiskit_aqua.quantum_instance._circuit_cache.misses, 0)
        self.assertIn('eval_count', result_caching)
        self.assertIn('eval_time', result_caching)

    @parameterized.expand([
        [True],
        [False]
    ])
    def atest_vqe_caching_direct(self, batch_mode=True):
        backend = BasicAer.get_backend('statevector_simulator')
        num_qubits = self.algo_input.qubit_op.num_qubits
        init_state = Zero(num_qubits)
        var_form = RY(num_qubits, 3, initial_state=init_state)
        optimizer = L_BFGS_B()
        algo = VQE(self.algo_input.qubit_op, var_form, optimizer, 'matrix', batch_mode=batch_mode)
        quantum_instance_caching = QuantumInstance(backend,
                                                   circuit_caching=True,
                                                   skip_qobj_deepcopy=True,
                                                   skip_qobj_validation=True)
        result_caching = algo.run(quantum_instance_caching)
        self.assertLessEqual(quantum_instance_caching.circuit_cache.misses, 0)
        self.assertAlmostEqual(self.reference_vqe_result['statevector_simulator']['energy'], result_caching['energy'])
        speedup_check = 3
        self.log.info(result_caching['eval_time'],
                      self.reference_vqe_result['statevector_simulator']['eval_time']/speedup_check)

    def atest_saving_and_loading(self):
        backend = BasicAer.get_backend('statevector_simulator')
        num_qubits = self.algo_input.qubit_op.num_qubits
        init_state = Zero(num_qubits)
        var_form = RY(num_qubits, 3, initial_state=init_state)
        optimizer = L_BFGS_B()
        algo = VQE(self.algo_input.qubit_op, var_form, optimizer, 'matrix')

        fd, cache_tmp_file = tempfile.mkstemp(suffix='.inp')
        os.close(fd)

        quantum_instance_caching = QuantumInstance(backend,
                                                   circuit_caching=True,
                                                   cache_file=cache_tmp_file,
                                                   skip_qobj_deepcopy=True,
                                                   skip_qobj_validation=True)
        algo.run(quantum_instance_caching)
        self.assertLessEqual(quantum_instance_caching.circuit_cache.misses, 0)

        is_file_exist = os.path.exists(cache_tmp_file)
        self.assertTrue(is_file_exist, "Does not store content successfully.")

        circuit_cache_new = CircuitCache(skip_qobj_deepcopy=True, cache_file=cache_tmp_file)
        self.assertEqual(quantum_instance_caching.circuit_cache.mappings, circuit_cache_new.mappings)
        self.assertLessEqual(circuit_cache_new.misses, 0)

        if is_file_exist:
            os.remove(cache_tmp_file)


if __name__ == '__main__':
    unittest.main()
