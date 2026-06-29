module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire reg2hw.alert_test.recov_operation_err.q,
    input wire reg2hw.alert_test.recov_operation_err.qe,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) !(reg2hw.alert_test.recov_operation_err.q && reg2hw.alert_test.recov_operation_err.qe));

endmodule


bind keymgr sva_checker sva_checker_inst (.*);
