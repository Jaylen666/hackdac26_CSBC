module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire ecc_errs,
    input wire key_state_d,
    input wire key_state_q,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (|ecc_errs) |-> (key_state_d != key_state_q));

endmodule


bind keymgr_ctrl sva_checker sva_checker_inst (.*);
