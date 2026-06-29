module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire clk_i,
    input wire rst_ni,
    input wire key_state_q,
    input wire key_state_d
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (1) |=> (key_state_q == $past(key_state_d)));

endmodule


bind keymgr_ctrl sva_checker sva_checker_inst (.*);
