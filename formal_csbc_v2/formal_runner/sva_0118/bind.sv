module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire vld_state_change_d,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) $stable(vld_state_change_d));

endmodule


bind keymgr_ctrl sva_checker sva_checker_inst (.*);
