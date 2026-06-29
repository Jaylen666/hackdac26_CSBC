module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire out_q,
    input wire rst_ni,
    input wire clk_i
);

assert property (@(posedge clk_i) disable iff (!rst_ni) ($rose(rst_ni) |-> out_q == 1'b0));

endmodule


bind keymgr_cfg_en sva_checker sva_checker_inst (.*);
