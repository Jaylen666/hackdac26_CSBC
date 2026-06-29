module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire vld_set,
    input wire out_clr,
    input wire en_i,
    input wire out_o,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (vld_set && out_clr && en_i) |-> (out_o == 1'b1));

endmodule


bind keymgr_cfg_en sva_checker sva_checker_inst (.*);
